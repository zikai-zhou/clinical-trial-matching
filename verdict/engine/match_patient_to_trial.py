#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .inference_engine_5 import AzureInferenceEngine
from .modules.SMTMatcher.SMTMatcher import SMTMatcher
from .utils import dict_to_readable_string

SIDES = ("inclusion", "exclusion")

_VAR_MINER_INFER_PROMPT_REL = "./prompt_out/smt.prompt"
_VAR_MINER_EXPLICIT_PROMPT_REL = "./prompt_out/smt.prompt"

_VAR_MINER_CONS_INFER_PROMPT_REL = "./prompt_out/smt.prompt"
_VAR_MINER_CONS_EXPLICIT_PROMPT_REL = "./prompt_out/smt.prompt"

_SCOPE_MINER_PROMPT_REL = "./prompt_out/SMTVariableScopeMiner.prompt"
_PROJECTION_REWRITER_PROMPT_REL = "./prompt_out/SMTVariableProjectionRewriter.prompt"

DEFAULT_ELIGIBILITY_PROMPT_REL = "./prompt_out/nl.prompt"

INCL_GPT4 = "inclusion_gpt4_eligibility"
INCL_EXPT = "inclusion_expert_eligibility"
EXCL_GPT4 = "exclusion_gpt4_eligibility"
EXCL_EXPT = "exclusion_expert_eligibility"

_NCT_PARENT_RE = re.compile(r"^(NCT\d{8})", re.IGNORECASE)
_RE_DECLARE_CONST = re.compile(r"^\s*\(declare-const\s+(\S+)\s+(.+)\)\s*$")

CACHE_SCHEMA_VERSION = "2026-03-17-prompt-fingerprint-v1"


def parent_nct_id(trial_id: str) -> str:
    if not isinstance(trial_id, str):
        return str(trial_id)
    m = _NCT_PARENT_RE.match(trial_id.strip())
    return (m.group(1).upper() if m else trial_id.strip())


def _normalize_miner_mode(mode: str | None) -> str:
    m = (mode or "infer").strip().lower()
    return m if m in ("infer", "explicit") else "infer"


def _miner_rel_path(*, mode: str, conservative: bool) -> str:
    m = _normalize_miner_mode(mode)
    if conservative:
        return _VAR_MINER_CONS_INFER_PROMPT_REL if m == "infer" else _VAR_MINER_CONS_EXPLICIT_PROMPT_REL
    return _VAR_MINER_INFER_PROMPT_REL if m == "infer" else _VAR_MINER_EXPLICIT_PROMPT_REL


def _json_default(o):
    import pathlib as _p
    import datetime as _d
    import re as _r

    if isinstance(o, set):
        try:
            return sorted(o)
        except TypeError:
            return list(o)
    if isinstance(o, (_p.Path,)):
        return str(o)
    if isinstance(o, (_d.date, _d.datetime)):
        return o.isoformat()
    if isinstance(o, _r.Pattern):
        return o.pattern
    return str(o)


def _stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=_json_default)


def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(_stable_json_dumps(obj).encode("utf-8")).hexdigest()


def _safe_read_json(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_write_json_atomic(path: pathlib.Path, obj: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        tmp.replace(path)
        return True
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def _cache_root_for_out_root(out_root: pathlib.Path) -> pathlib.Path:
    return out_root / "_prompt_cache"


def _cache_file_for_fingerprint(out_root: pathlib.Path, stage: str, fingerprint: str) -> pathlib.Path:
    return _cache_root_for_out_root(out_root) / stage / f"{fingerprint}.json"


def _cache_payload_matches(payload: Optional[Dict[str, Any]], fingerprint: str) -> bool:
    if not isinstance(payload, dict):
        return False
    cache = payload.get("cache")
    if not isinstance(cache, dict):
        return False
    return (
        cache.get("schema_version") == CACHE_SCHEMA_VERSION
        and cache.get("fingerprint") == fingerprint
    )


def _extract_first_json_object_from_line(line: str) -> Optional[str]:
    start = line.find("{")
    if start < 0:
        return None

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(line)):
        ch = line[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        else:
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return line[start : i + 1]
    return None


def _best_defs_smt_path(trial_id: str, side: str, cfg: "Config") -> Optional[pathlib.Path]:
    defs_dir = getattr(cfg, "ir_defs_dir", None)
    if defs_dir is None:
        return None

    defs_dir = pathlib.Path(defs_dir)
    if not defs_dir.exists():
        return None

    def _pick(glob_pat: str) -> Optional[pathlib.Path]:
        cands = list(defs_dir.glob(glob_pat))
        if not cands:
            return None
        cands.sort(key=lambda p: (len(p.name), p.name))
        return cands[0]

    p = _pick(f"{trial_id}_{side}_program*.smt2")
    if p is not None:
        return p

    parent = parent_nct_id(trial_id)
    if parent != trial_id:
        p2 = _pick(f"{parent}_{side}_program*.smt2")
        if p2 is not None:
            return p2

    return None


def _parse_var_definitions_from_annotated_smt(smt_text: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for line in (smt_text or "").splitlines():
        if "(declare-const" not in line:
            continue

        code = line.split(";", 1)[0].rstrip()
        m = _RE_DECLARE_CONST.match(code)
        if not m:
            continue

        var = m.group(1)
        sort = m.group(2).strip()

        jb = _extract_first_json_object_from_line(line)
        if not jb:
            continue

        try:
            obj = json.loads(jb)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue

        d = dict(obj)
        d["__sort__"] = sort
        out[var] = d

    return out


# Vendored into this repository; see docs/MATCHERS.md. Upstream resolved these
# roots against the current working directory, which only worked when run from
# inside the matcher directory. They are now anchored to the package and
# overridable by environment variable so the matcher runs from anywhere.
_PKG = pathlib.Path(__file__).resolve().parent
_REPO = _PKG.parents[1]


def _root(env: str, default: pathlib.Path) -> pathlib.Path:
    v = os.getenv(env)
    return pathlib.Path(v).expanduser() if v else default


@dataclass
class Config:
    data_root: pathlib.Path = _root("TRIAL_DATA", _REPO / "data" / "clinical_trial")
    build_root: pathlib.Path = _root("VERDICT_BUILD", _REPO / "build")
    project_root: pathlib.Path = _REPO
    prompt_root: pathlib.Path = _PKG / "prompts"
    prompt_map_json: pathlib.Path | None = None

    conservative: bool = False
    miner_mode: str = "infer"

    azure_endpoint: str = os.getenv("OPENAI_ENDPOINT", "")
    model_name: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    ir_dir: pathlib.Path = field(init=False)
    symtab_dir: pathlib.Path = field(init=False)
    linkmap_dir: pathlib.Path = field(init=False)
    ir_defs_dir: pathlib.Path = field(init=False)

    def __post_init__(self):
        self.miner_mode = _normalize_miner_mode(self.miner_mode)
        self.ir_dir = self.build_root / "noslice_ir_linked"
        self.symtab_dir = self.build_root / "symtab"
        self.linkmap_dir = self.build_root / "linkmap"
        self.ir_defs_dir = self.build_root / "ir"

    def prompt_sources(self) -> Dict[str, pathlib.Path]:
        base = self.prompt_root if self.prompt_root.is_absolute() else (_PKG / self.prompt_root)
        miner_rel = _miner_rel_path(mode=self.miner_mode, conservative=self.conservative)

        mapping: Dict[str, pathlib.Path] = {
            "SMTVariableValueMinerInclusion_prompt": (base / miner_rel).resolve(),
            "SMTVariableValueMinerExclusion_prompt": (base / miner_rel).resolve(),
            "SMTVariableScopeMiner_prompt": (base / _SCOPE_MINER_PROMPT_REL).resolve(),
            "SMTVariableProjectionRewriter_prompt": (base / _PROJECTION_REWRITER_PROMPT_REL).resolve(),
        }

        if self.prompt_map_json:
            raw = json.loads(self.prompt_map_json.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("--prompt-map must be a JSON object {key: path}")
            for k, v in raw.items():
                p = pathlib.Path(v)
                if not p.is_absolute():
                    p = base / p
                mapping[k] = p.resolve()

        return mapping

    def eligibility_prompt_path(self, override: str | None) -> pathlib.Path:
        base = self.prompt_root if self.prompt_root.is_absolute() else (_PKG / self.prompt_root)
        if override:
            p = pathlib.Path(override)
            if not p.is_absolute():
                p = base / p
            return p.resolve()
        return (base / DEFAULT_ELIGIBILITY_PROMPT_REL).resolve()

    def persisted_paths(self, trial_id: str, side: str) -> Dict[str, pathlib.Path]:
        return {
            "smt": self.ir_dir / f"{trial_id}_{side}_program.smt2",
            "var": self.symtab_dir / f"{trial_id}_{side}_variable_index.json",
            "ent": self.linkmap_dir / f"{trial_id}_{side}_entities.json",
            "canon": self.build_root / "canon" / f"{trial_id}_{side}_canonical_variables.json",
            "defs": self.ir_defs_dir / f"{trial_id}_{side}_program.smt2",
        }


def _status_bool(status: str | None) -> bool | None:
    if not isinstance(status, str):
        return None
    s = status.lower()
    if s == "sat":
        return True
    if s == "unsat":
        return False
    return None


def ensure_prompt_templates(ctx: Dict[str, Any], *, pathmap: Dict[str, pathlib.Path]) -> Dict[str, Any]:
    updates: Dict[str, str] = {}
    warnings: List[str] = []

    for key, path in pathmap.items():
        try:
            updates[key] = path.read_text(encoding="utf-8")
            continue
        except FileNotFoundError:
            if "SMTVariableValueMiner" in key and "Conservative" in path.name:
                alt_name = path.name.replace("Conservative", "")
                alt_path = path.with_name(alt_name)
                try:
                    updates[key] = alt_path.read_text(encoding="utf-8")
                    warnings.append(f"[prompt_fallback] missing {path.name}; used {alt_path.name}")
                    continue
                except FileNotFoundError:
                    pass
            raise FileNotFoundError(f"Prompt not found for key '{key}': {path}")

    if warnings:
        ctx.setdefault("PROMPT_WARNINGS", [])
        if isinstance(ctx["PROMPT_WARNINGS"], list):
            ctx["PROMPT_WARNINGS"].extend(warnings)

    ctx.update(updates)
    return ctx


_SIGIR_RE = re.compile(r"^sigir-\d{5,}$", re.IGNORECASE)
_TREC21_RE = re.compile(r"^trec-2021\d+$", re.IGNORECASE)
_TREC22_RE = re.compile(r"^trec-2022\d+$", re.IGNORECASE)


def _infer_query_dataset_dir(patient_id: str) -> str | None:
    if _SIGIR_RE.match(patient_id):
        return "sigir"
    if _TREC21_RE.match(patient_id):
        return "trec_2021"
    if _TREC22_RE.match(patient_id):
        return "trec_2022"
    return None


def _load_from_queries_jsonl(jsonl_path: pathlib.Path, patient_id: str) -> Dict[str, Any]:
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Queries file not found: {jsonl_path}")
    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("_id") == patient_id:
                return {
                    "patient_id": patient_id,
                    "text": obj.get("text", ""),
                    "metadata": obj.get("metadata", {}),
                    "_raw": obj,
                }
    raise FileNotFoundError(f"Patient id '{patient_id}' not found in {jsonl_path}")


def load_patient(patient_id: str, data_root: pathlib.Path, patient_file: str | None) -> Dict[str, Any]:
    if patient_file:
        p = pathlib.Path(patient_file)
        if not p.exists():
            raise FileNotFoundError(f"Patient file not found: {p}")
        obj = json.loads(p.read_text(encoding="utf-8"))
        obj.setdefault("patient_id", patient_id)
        return obj

    subdir = _infer_query_dataset_dir(patient_id)
    if subdir:
        return _load_from_queries_jsonl(data_root / subdir / "queries.jsonl", patient_id)

    guess = data_root / "patients" / f"{patient_id}.json"
    if not guess.exists():
        raise FileNotFoundError(
            f"Patient not found. Tried: {guess}\n"
            "Provide --patient-file or use a known ID (sigir-*, trec-2021*, trec-2022*)."
        )
    obj = json.loads(guess.read_text(encoding="utf-8"))
    obj.setdefault("patient_id", patient_id)
    return obj


def extract_patient_note_text(patient: Dict[str, Any]) -> str:
    for k in ("text", "note", "content", "summary"):
        v = patient.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return dict_to_readable_string(patient).strip()


def build_ctx_from_persisted(trial_id: str, side: str, cfg: Config) -> Dict[str, Any]:
    paths = cfg.persisted_paths(trial_id, side)

    if not paths["smt"].exists():
        raise FileNotFoundError(
            f"SMT program missing: {paths['smt']}\n"
            "Run the programmer so it persists SMT, or point --build-root correctly."
        )

    ctx: Dict[str, Any] = {
        "trial_id": trial_id,
        "inc_exc": side,
        "variable_index": {},
        "build_root": str(cfg.build_root.resolve()),
        "project_root": str(cfg.project_root.resolve()),
    }

    smt_text = paths["smt"].read_text(encoding="utf-8", errors="ignore")
    ctx["smt_program_lines"] = smt_text.splitlines()

    if paths["ent"].exists():
        ctx["requirement_bundles"] = json.loads(paths["ent"].read_text(encoding="utf-8"))

    if paths["canon"].exists():
        canon_obj = json.loads(paths["canon"].read_text(encoding="utf-8"))
        ctx["canonical_variables"] = canon_obj.get("canonical_variables", canon_obj)
    else:
        ctx["canonical_variables"] = []

    defs_path = _best_defs_smt_path(trial_id, side, cfg)
    if defs_path is not None and defs_path.exists():
        defs_text = defs_path.read_text(encoding="utf-8", errors="ignore")
        ctx["var_definitions"] = _parse_var_definitions_from_annotated_smt(defs_text)
        ctx["var_definitions_source"] = str(defs_path)
    else:
        ctx["var_definitions"] = {}
        ctx["var_definitions_source"] = None

    return ctx


def _norm_text(v: Any) -> str:
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    if v is None:
        return ""
    return str(v)


def _attach_compact_trial_context(ctx: Dict[str, Any], trial_id: str, side: str, cfg: Config) -> Dict[str, Any]:
    try:
        trial_obj = load_trial_record(trial_id, cfg)
    except Exception:
        trial_obj = {}

    md = trial_obj.get("metadata") or {}

    ctx["encoding_side"] = side
    ctx["trial_id_parent"] = parent_nct_id(trial_id)
    ctx["trial_title"] = (
        trial_obj.get("brief_title")
        or trial_obj.get("title")
        or trial_obj.get("official_title")
        or md.get("brief_title")
        or ""
    )
    ctx["trial_inclusion_criteria"] = _norm_text(
        trial_obj.get("inclusion_criteria") or md.get("inclusion_criteria")
    )
    ctx["trial_exclusion_criteria"] = _norm_text(
        trial_obj.get("exclusion_criteria") or md.get("exclusion_criteria")
    )
    return ctx


def _coarse_smt_forward_fingerprint(ctx: Dict[str, Any], cfg: Config) -> str:
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "stage": "smt_forward",
        "trial_id": ctx.get("trial_id"),
        "trial_id_parent": ctx.get("trial_id_parent"),
        "patient_id": ctx.get("patient_id"),
        "side": ctx.get("inc_exc"),
        "model_name": cfg.model_name,
        "miner_mode": ctx.get("VAR_MINER_MODE"),
        "conservative": bool(cfg.conservative),
        "canonical_only": bool(ctx.get("USE_CANONICAL_MINER")),
        "vv_batch_size": ctx.get("VV_BATCH_SIZE"),
        "whole_program": bool(ctx.get("WHOLE_PROGRAM")),
        "eval_use_rich_patient_values": ctx.get("EVAL_USE_RICH_PATIENT_VALUES"),
        "patient_notes": ctx.get("patient_notes"),
        "smt_program_lines": ctx.get("smt_program_lines"),
        "requirement_bundles": ctx.get("requirement_bundles"),
        "canonical_variables": ctx.get("canonical_variables"),
        "var_definitions": ctx.get("var_definitions"),
        "trial_title": ctx.get("trial_title"),
        "trial_inclusion_criteria": ctx.get("trial_inclusion_criteria"),
        "trial_exclusion_criteria": ctx.get("trial_exclusion_criteria"),
        "prompt_scope": ctx.get("SMTVariableScopeMiner_prompt"),
        "prompt_projection": ctx.get("SMTVariableProjectionRewriter_prompt"),
        "prompt_vv_incl": ctx.get("SMTVariableValueMinerInclusion_prompt"),
        "prompt_vv_excl": ctx.get("SMTVariableValueMinerExclusion_prompt"),
    }
    return _stable_hash(payload)


def run_match_for_side(
    side: str,
    trial_id: str,
    patient: Dict[str, Any],
    cfg: Config,
    engine: AzureInferenceEngine,
    labels_obj: Optional[Dict[str, Any]] = None,
    args: Optional[argparse.Namespace] = None,
) -> Dict[str, Any]:
    ctx = build_ctx_from_persisted(trial_id, side, cfg)
    ctx = _attach_compact_trial_context(ctx, trial_id, side, cfg)

    if args and args.canonical_only:
        if not ctx.get("canonical_variables"):
            raise FileNotFoundError(
                f"Canonical variables missing for {trial_id} {side}. "
                "Generate build/canon/*_canonical_variables.json or pass --canonical-json."
            )

    if not (args and args.canonical_only):
        ctx = ensure_prompt_templates(ctx, pathmap=cfg.prompt_sources())

    ctx["patient_id"] = patient.get("patient_id") or patient.get("_id") or patient.get("id") or "UNKNOWN"
    ctx["patient"] = patient
    ctx["patient_contextual_text"] = dict_to_readable_string(patient)

    note_text = extract_patient_note_text(patient)
    if note_text:
        ctx["patient_notes"] = [note_text]
        ctx["patient_notes_db"] = [{"_id": ctx["patient_id"], "text": note_text}]

    ctx.setdefault("MBENCH_ENABLED", True)
    ctx.setdefault("MBENCH_ROOT", "mbench")
    ctx["MBENCH_TS"] = False
    ctx["MBENCH_RUN_LABEL"] = f"{trial_id}_{side}_{ctx['patient_id']}"

    if args:
        ctx["USE_CANONICAL_MINER"] = bool(args.canonical_only)
        ctx["VV_BATCH_SIZE"] = int(args.vv_batch_size or 10)
        ctx["VERBOSE"] = bool(args.verbose)
        ctx["WHOLE_PROGRAM"] = True
        ctx["VAR_MINER_MODE"] = str(getattr(args, "miner_mode", cfg.miner_mode) or cfg.miner_mode)

    # Ablation toggle: EVAL_USE_RICH_PATIENT_VALUES controls whether the
    # whole-program evaluator consumes the rich patient-value dict (raw value +
    # numeric range bounds + projected threshold-level assessment) or the flat
    # raw-value-only dict. Default True (paper config). Set the env var to
    # 0/false to disable the threshold dual-extraction for ablation.
    _rich_env = os.getenv("EVAL_USE_RICH_PATIENT_VALUES", "").strip().lower()
    if _rich_env in ("0", "false", "no", "off"):
        ctx["EVAL_USE_RICH_PATIENT_VALUES"] = False
        ctx["ENABLE_NUMERIC_RANGE_ASSERTS"] = False
    elif _rich_env in ("1", "true", "yes", "on"):
        ctx["EVAL_USE_RICH_PATIENT_VALUES"] = True

    # Threshold ablation: when DUAL_EVAL_THRESHOLD_ABLATION is set, the matcher
    # mines once and evaluates twice (rich + flat) on the same mined values, so
    # the ablation delta is free of mining nondeterminism.
    if os.getenv("DUAL_EVAL_THRESHOLD_ABLATION", "").strip().lower() in ("1", "true", "yes", "on"):
        ctx["DUAL_EVAL_THRESHOLD_ABLATION"] = True
        if args.canonical_json:
            p = pathlib.Path(args.canonical_json)
            if p.exists():
                try:
                    obj = json.loads(p.read_text(encoding="utf-8"))
                    ctx["canonical_variables"] = obj.get("canonical_variables", obj)
                except Exception as e:
                    print(f"[warn] Failed to read --canonical-json: {e}", file=sys.stderr)

    if labels_obj:
        if side == "inclusion":
            ctx["labels"] = {
                "gpt4": list(labels_obj.get(INCL_GPT4, []) or []),
                "expert": list(labels_obj.get(INCL_EXPT, []) or []),
            }
            ctx["criteria"] = list(labels_obj.get("inclusion_criteria", []) or [])
        else:
            ctx["labels"] = {
                "gpt4": list(labels_obj.get(EXCL_GPT4, []) or []),
                "expert": list(labels_obj.get(EXCL_EXPT, []) or []),
            }
            ctx["criteria"] = list(labels_obj.get("exclusion_criteria", []) or [])

    out_root = pathlib.Path(getattr(args, "out_root", "match_out")).resolve() if args else pathlib.Path("match_out").resolve()
    fingerprint = _coarse_smt_forward_fingerprint(ctx, cfg)
    cache_path = _cache_file_for_fingerprint(out_root, f"smt_forward_{side}", fingerprint)

    cached = _safe_read_json(cache_path)
    if _cache_payload_matches(cached, fingerprint):
        result_ctx = cached.get("result_ctx")
        if isinstance(result_ctx, dict):
            whole = (result_ctx.get("eval_results") or {}).get("whole_program") or {}
            stats = result_ctx.get("stats") or {}
            _abl_whole = (result_ctx.get("eval_results") or {}).get("whole_program_ablation")
            return {
                "side": side,
                "sat_like": _status_bool(whole.get("status")),
                "sat_like_ablation": (_status_bool(_abl_whole.get("status"))
                                      if isinstance(_abl_whole, dict) else None),
                "summary": (stats.get("whole_program") or {}),
                "raw": result_ctx,
                "cache": {
                    "used": True,
                    "fingerprint": fingerprint,
                    "path": str(cache_path),
                },
            }

    result_ctx = SMTMatcher(engine).forward(ctx)

    cache_obj = {
        "cache": {
            "schema_version": CACHE_SCHEMA_VERSION,
            "stage": f"smt_forward_{side}",
            "fingerprint": fingerprint,
            "trial_id": trial_id,
            "patient_id": ctx["patient_id"],
            "model_name": cfg.model_name,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
        "result_ctx": result_ctx,
    }
    _safe_write_json_atomic(cache_path, cache_obj)

    whole = (result_ctx.get("eval_results") or {}).get("whole_program") or {}
    whole_status = whole.get("status")
    sat_like = _status_bool(whole_status)

    stats = result_ctx.get("stats") or {}
    summary = stats.get("whole_program") or {}

    # Threshold-ablation dual eval (if enabled): the same mine, evaluated with
    # EVAL_USE_RICH_PATIENT_VALUES flipped.
    ablation_whole = (result_ctx.get("eval_results") or {}).get("whole_program_ablation")
    sat_like_ablation = None
    if isinstance(ablation_whole, dict):
        sat_like_ablation = _status_bool(ablation_whole.get("status"))

    return {
        "side": side,
        "sat_like": sat_like,
        "sat_like_ablation": sat_like_ablation,
        "summary": summary,
        "raw": result_ctx,
        "cache": {
            "used": False,
            "fingerprint": fingerprint,
            "path": str(cache_path),
        },
    }


_TRIAL_RECORD_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _corpus_jsonl_paths(cfg: Config) -> List[pathlib.Path]:
    override = os.getenv("TRIAL_CORPUS_JSONL", "").strip()
    if override:
        parts = [p for p in override.split(os.pathsep) if p.strip()]
        return [pathlib.Path(p).expanduser().resolve() for p in parts]

    return [
        (cfg.data_root / "sigir" / "corpus.jsonl"),
        (cfg.data_root / "trec_2021" / "corpus.jsonl"),
        (cfg.data_root / "trec_2022" / "corpus.jsonl"),
    ]


def _normalize_trial_obj_from_corpus_line(obj: Dict[str, Any]) -> Dict[str, Any]:
    md = obj.get("metadata") or {}
    out = dict(obj)
    if isinstance(md, dict):
        out.setdefault("brief_title", md.get("brief_title") or obj.get("title") or obj.get("brief_title") or "")
        out.setdefault("brief_summary", md.get("brief_summary") or "")
        out.setdefault("inclusion_criteria", md.get("inclusion_criteria") or "")
        out.setdefault("exclusion_criteria", md.get("exclusion_criteria") or "")
        out.setdefault("diseases_list", md.get("diseases_list") or [])
        out.setdefault("drugs_list", md.get("drugs_list") or [])
    else:
        out.setdefault("brief_title", obj.get("title") or "")
        out.setdefault("brief_summary", "")
        out.setdefault("inclusion_criteria", "")
        out.setdefault("exclusion_criteria", "")
        out.setdefault("diseases_list", [])
        out.setdefault("drugs_list", [])

    out.setdefault("description", obj.get("text") or "")
    out.setdefault("title", obj.get("title") or out.get("brief_title") or "")
    return out


def _load_trial_from_corpus_jsonl(trial_id: str, corpus_path: pathlib.Path) -> Optional[Dict[str, Any]]:
    if not corpus_path.exists():
        return None
    with corpus_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("_id") == trial_id or obj.get("id") == trial_id:
                return _normalize_trial_obj_from_corpus_line(obj)
    return None


def load_trial_record(trial_id: str, cfg: Config) -> Dict[str, Any]:
    parent_id = parent_nct_id(trial_id)

    cache_key = (str(cfg.data_root.resolve()), parent_id)
    if cache_key in _TRIAL_RECORD_CACHE:
        return _TRIAL_RECORD_CACHE[cache_key]

    candidates = [
        cfg.data_root / "trials" / f"{parent_id}.json",
        cfg.data_root / "trial" / f"{parent_id}.json",
        cfg.data_root / "clinical_trials" / f"{parent_id}.json",
        cfg.data_root / f"{parent_id}.json",
    ]
    for p in candidates:
        if p.exists():
            obj = json.loads(p.read_text(encoding="utf-8"))
            _TRIAL_RECORD_CACHE[cache_key] = obj
            return obj

    for cp in _corpus_jsonl_paths(cfg):
        found = _load_trial_from_corpus_jsonl(parent_id, cp)
        if found is not None:
            _TRIAL_RECORD_CACHE[cache_key] = found
            return found

    raise FileNotFoundError(
        "Trial JSON not found for LLM judge. Tried:\n  - "
        + "\n  - ".join(str(x) for x in candidates + _corpus_jsonl_paths(cfg))
    )


def format_trial_description(trial_obj: Dict[str, Any]) -> str:
    title = trial_obj.get("brief_title") or trial_obj.get("title") or trial_obj.get("official_title") or ""
    summary = trial_obj.get("brief_summary") or trial_obj.get("summary") or trial_obj.get("description") or ""
    inc = trial_obj.get("inclusion_criteria") or trial_obj.get("inclusion") or ""
    exc = trial_obj.get("exclusion_criteria") or trial_obj.get("exclusion") or ""

    if isinstance(inc, list):
        inc = "\n".join(str(x) for x in inc)
    if isinstance(exc, list):
        exc = "\n".join(str(x) for x in exc)

    parts = []
    if title:
        parts.append(f"Title: {title}")
    if summary:
        parts.append(f"Summary:\n{summary}")
    if inc:
        parts.append(f"Inclusion Criteria:\n{inc}")
    if exc:
        parts.append(f"Exclusion Criteria:\n{exc}")
    if not parts:
        parts.append(json.dumps(trial_obj, ensure_ascii=False, indent=2))
    return "\n\n".join(parts).strip()


def render_eligibility_prompt(template: str, trial_desc: str, patient_note: str) -> str:
    return template.replace("#CLINICAL_TRIAL_DESCRIPTION#", trial_desc).replace("#PATIENT_NOTE#", patient_note)


def _call_engine_text(engine: Any, prompt: str, *, temperature: Optional[float] = 0.0) -> str:
    messages = [{"role": "user", "content": prompt}]

    def _extract_text(out: Any) -> Optional[str]:
        if isinstance(out, str):
            return out
        if isinstance(out, dict):
            if isinstance(out.get("text"), str):
                return out["text"]
            if isinstance(out.get("content"), str):
                return out["content"]
            choices = out.get("choices")
            if isinstance(choices, list) and choices:
                c0 = choices[0]
                if isinstance(c0, dict):
                    msg = c0.get("message")
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        return msg["content"]
                    if isinstance(c0.get("text"), str):
                        return c0["text"]
        return None

    last_err: Optional[Exception] = None

    for method_name in ("chat_complete", "complete", "invoke", "run", "generate", "completion"):
        m = getattr(engine, method_name, None)
        if not callable(m):
            continue

        try:
            try:
                out = m(prompt, temperature=temperature)
            except TypeError:
                out = m(prompt)
            text = _extract_text(out)
            if text is not None:
                return text
        except Exception as e:
            last_err = e

        try:
            try:
                out = m(messages, temperature=temperature)
            except TypeError:
                out = m(messages)
            text = _extract_text(out)
            if text is not None:
                return text
        except Exception as e:
            last_err = e

        try:
            out = m(messages=messages, temperature=temperature)
            text = _extract_text(out)
            if text is not None:
                return text
        except Exception as e:
            last_err = e

    if callable(engine):
        for call in (
            (lambda: engine(messages=messages, temperature=temperature)),
            (lambda: engine(messages)),
            (lambda: engine(prompt)),
        ):
            try:
                out = call()
                text = _extract_text(out)
                if text is not None:
                    return text
            except Exception as e:
                last_err = e

    raise RuntimeError(
        "Could not call AzureInferenceEngine with prompt or chat messages. "
        f"Last error: {type(last_err).__name__}: {last_err}"
    )


def parse_eligibility_judge_output(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()

    m_reason = re.search(
        r"<reasoning>\s*(.*?)\s*</reasoning>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    reasoning = m_reason.group(1).strip() if m_reason else None

    m = re.search(
        r"<eligibility_decision>\s*(\{.*?\})\s*</eligibility_decision>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    json_str = m.group(1).strip() if m else None
    if json_str is None:
        m2 = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        json_str = m2.group(1).strip() if m2 else None

    obj: Dict[str, Any] = {}
    parse_error = None
    if json_str:
        try:
            obj = json.loads(json_str)
        except Exception as e:
            parse_error = f"json_parse_error: {e}"
            obj = {}
    else:
        parse_error = "no_json_found"

    elig = obj.get("eligibility")
    eligible_bool: Optional[bool] = None
    if isinstance(elig, str):
        s = elig.strip().lower()
        if s == "eligible":
            eligible_bool = True
        elif s == "ineligible":
            eligible_bool = False

    return {
        "eligible": eligible_bool,
        "eligibility": elig,
        "reasoning": reasoning,
        "explanation": obj.get("explanation"),
        "parse_error": parse_error,
        "raw_text": raw_text,
    }


def _llm_judge_fingerprint(
    *,
    template: str,
    trial_desc: str,
    patient_note: str,
    model_name: str,
    temperature: float,
    prompt_path: pathlib.Path,
    trial_id: str,
    patient_id: str,
) -> str:
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "stage": "llm_judge",
        "template": template,
        "trial_desc": trial_desc,
        "patient_note": patient_note,
        "model_name": model_name,
        "temperature": temperature,
        "prompt_path": str(prompt_path),
        "trial_id": trial_id,
        "patient_id": patient_id,
    }
    return _stable_hash(payload)


def run_llm_eligibility_judge(
    trial_id: str,
    patient: Dict[str, Any],
    cfg: Config,
    engine: AzureInferenceEngine,
    *,
    prompt_path: pathlib.Path,
    out_root: pathlib.Path,
    temperature: float = 0.0,
) -> Dict[str, Any]:
    template = prompt_path.read_text(encoding="utf-8")

    parent_id = parent_nct_id(trial_id)
    patient_id = patient.get("patient_id") or patient.get("_id") or patient.get("id")
    trial_obj = load_trial_record(parent_id, cfg)
    trial_desc = format_trial_description(trial_obj)
    patient_note = extract_patient_note_text(patient)

    fingerprint = _llm_judge_fingerprint(
        template=template,
        trial_desc=trial_desc,
        patient_note=patient_note,
        model_name=cfg.model_name,
        temperature=temperature,
        prompt_path=prompt_path,
        trial_id=parent_id,
        patient_id=str(patient_id),
    )

    cache_path = _cache_file_for_fingerprint(out_root, "llm_judge", fingerprint)
    cached = _safe_read_json(cache_path)
    if _cache_payload_matches(cached, fingerprint):
        result = cached.get("payload")
        if isinstance(result, dict):
            return result

    prompt = render_eligibility_prompt(template, trial_desc, patient_note)
    raw = _call_engine_text(engine, prompt, temperature=temperature)
    parsed = parse_eligibility_judge_output(raw)

    payload = {
        "cache": {
            "schema_version": CACHE_SCHEMA_VERSION,
            "stage": "llm_judge",
            "fingerprint": fingerprint,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "model_name": cfg.model_name,
            "temperature": temperature,
            "cache_path": str(cache_path),
        },
        "trial_id_original": trial_id,
        "trial_id_parent": parent_id,
        "patient_id": patient_id,
        "prompt_path": str(prompt_path),
        "temperature": temperature,
        "result": parsed,
    }

    _safe_write_json_atomic(cache_path, {"cache": payload["cache"], "payload": payload})
    return payload


def _maybe_parse_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            if s.startswith("[") and s.endswith("]"):
                x = json.loads(s)
                if isinstance(x, list):
                    return [str(i) for i in x]
        except Exception:
            pass
        try:
            x = ast.literal_eval(s)
            if isinstance(x, list):
                return [str(i) for i in x]
        except Exception:
            pass
    return []


def _sent_tokenize_best_effort(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    try:
        from nltk.tokenize import sent_tokenize as _st  # type: ignore
        sents = _st(t)
        return [s.strip() for s in sents if isinstance(s, str) and s.strip()]
    except Exception:
        parts = re.split(r"(?<=[.!?])\s+", t)
        return [p.strip() for p in parts if p.strip()]


def _trialgpt_patient_with_sentence_ids(patient: Dict[str, Any]) -> Dict[str, Any]:
    note = extract_patient_note_text(patient)
    sents = _sent_tokenize_best_effort(note)
    sents.append("The patient will provide informed consent, and will comply with the trial protocol without any practical issues.")
    sents = [s.strip() for s in sents if s.strip()]
    numbered = [f"{i}. {s}" for i, s in enumerate(sents)]
    return {"patient_note_sentenced": "\n".join(numbered), "sentences": sents}


def _trialgpt_parse_criteria_list(criteria: Any) -> List[str]:
    if not isinstance(criteria, str):
        return []
    out: List[str] = []
    blocks = criteria.split("\n\n")
    for b in blocks:
        c = (b or "").strip()
        if len(c) < 5:
            continue
        low = c.lower()
        if "inclusion criteria" in low or "exclusion criteria" in low:
            continue
        out.append(c)
    return out


def _trialgpt_render_trial(trial_obj: Dict[str, Any], inc_exc: str) -> Tuple[str, List[str]]:
    md = trial_obj.get("metadata") or {}
    title = trial_obj.get("brief_title") or md.get("brief_title") or trial_obj.get("title") or ""
    summary = trial_obj.get("brief_summary") or md.get("brief_summary") or trial_obj.get("description") or ""

    diseases_list = _maybe_parse_list(trial_obj.get("diseases_list") or md.get("diseases_list") or [])
    drugs_list = _maybe_parse_list(trial_obj.get("drugs_list") or md.get("drugs_list") or [])

    inc_text = trial_obj.get("inclusion_criteria") or md.get("inclusion_criteria") or ""
    exc_text = trial_obj.get("exclusion_criteria") or md.get("exclusion_criteria") or ""

    criteria_list = _trialgpt_parse_criteria_list(inc_text if inc_exc == "inclusion" else exc_text)
    criteria_block = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria_list))

    trial = f"Title: {title}\n"
    trial += f"Target diseases: {', '.join(diseases_list) if diseases_list else ''}\n"
    trial += f"Interventions: {', '.join(drugs_list) if drugs_list else ''}\n"
    trial += f"Summary: {summary}\n"
    if inc_exc == "inclusion":
        trial += f"Inclusion criteria:\n {criteria_block}\n"
    else:
        trial += f"Exclusion criteria:\n {criteria_block}\n"

    return trial, criteria_list


def _trialgpt_prompts(trial_obj: Dict[str, Any], inc_exc: str, patient_sentenced: str) -> Tuple[str, str, List[str]]:
    trial_str, criteria_list = _trialgpt_render_trial(trial_obj, inc_exc)

    system_prompt = (
        "You are a helpful assistant for clinical trial recruitment. "
        f"Your task is to compare a given patient note and the {inc_exc} criteria of a clinical trial "
        "to determine the patient's eligibility at the criterion level.\n"
    )

    if inc_exc == "inclusion":
        system_prompt += (
            "The factors that allow someone to participate in a clinical study are called inclusion criteria. "
            "They are based on characteristics such as age, gender, the type and stage of a disease, previous treatment history, and other medical conditions.\n"
        )
    else:
        system_prompt += (
            "The factors that disqualify someone from participating are called exclusion criteria. "
            "They are based on characteristics such as age, gender, the type and stage of a disease, previous treatment history, and other medical conditions.\n"
        )

    system_prompt += (
        f"You should check the {inc_exc} criteria one-by-one, and output the following three elements for each criterion:\n"
        f"\tElement 1. For each {inc_exc} criterion, briefly generate your reasoning process: First, judge whether the criterion is not applicable (not very common), where the patient does not meet the premise of the criterion. Then, check if the patient note contains direct evidence. If so, judge whether the patient meets or does not meet the criterion. If there is no direct evidence, try to infer from existing evidence, and answer one question: If the criterion is true, is it possible that a good patient note will miss such information? If impossible, then you can assume that the criterion is not true. Otherwise, there is not enough information.\n"
        "\tElement 2. If there is relevant information, you must generate a list of relevant sentence IDs in the patient note. If there is no relevant information, you must annotate an empty list.\n"
        f"\tElement 3. Classify the patient eligibility for this specific {inc_exc} criterion: "
    )

    if inc_exc == "inclusion":
        system_prompt += (
            'the label must be chosen from {"not applicable", "not enough information", "included", "not included"}. '
            '"not applicable" should only be used for criteria that are not applicable to the patient. '
            '"not enough information" should be used where the patient note does not contain sufficient information for making the classification. '
            'Try to use as less "not enough information" as possible because if the note does not mention a medically important fact, you can assume that the fact is not true for the patient. '
            '"included" denotes that the patient meets the inclusion criterion, while "not included" means the reverse.\n'
        )
    else:
        system_prompt += (
            'the label must be chosen from {"not applicable", "not enough information", "excluded", "not excluded"}. '
            '"not applicable" should only be used for criteria that are not applicable to the patient. '
            '"not enough information" should be used where the patient note does not contain sufficient information for making the classification. '
            'Try to use as less "not enough information" as possible because if the note does not mention a medically important fact, you can assume that the fact is not true for the patient. '
            '"excluded" denotes that the patient meets the exclusion criterion and should be excluded in the trial, while "not excluded" means the reverse.\n'
        )

    system_prompt += (
        "You should output only a JSON dict exactly formatted as: "
        "dict{str(criterion_number): list[str(element_1_brief_reasoning), list[int(element_2_sentence_id)], str(element_3_eligibility_label)]}."
    )

    user_prompt = (
        "Here is the patient note, each sentence is led by a sentence_id:\n"
        f"{patient_sentenced}\n\n"
        f"Here is the clinical trial:\n{trial_str}\n\n"
        "Plain JSON output:"
    )

    return system_prompt, user_prompt, criteria_list


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_first_json_object(text: str) -> Optional[str]:
    t = _strip_code_fences(text)
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        else:
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return t[start : i + 1]
    return None


def _call_engine_chat(
    engine: Any,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: Optional[float] = None,
    **kwargs,
) -> str:
    try:
        from azure.ai.inference.models import SystemMessage, UserMessage  # type: ignore

        if hasattr(engine, "run") and callable(getattr(engine, "run")):
            msgs = [SystemMessage(content=system_prompt), UserMessage(content=user_prompt)]
            if temperature is None:
                return engine.run(msgs, **kwargs)
            return engine.run(msgs, temperature=float(temperature), **kwargs)
    except Exception:
        pass

    if callable(engine):
        try:
            if temperature is None:
                out = engine(user_prompt, system_message=system_prompt, **kwargs)
            else:
                out = engine(user_prompt, system_message=system_prompt, temperature=float(temperature), **kwargs)
            if isinstance(out, list) and out and isinstance(out[0], str):
                return out[0]
            if isinstance(out, str):
                return out
        except Exception:
            pass

    flat = f"{system_prompt}\n\n{user_prompt}"
    return _call_engine_text(engine, flat, temperature=temperature)


def _trialgpt_parse_output(raw_text: str, *, n_criteria: int, inc_exc: str) -> Dict[str, Any]:
    js = _extract_first_json_object(raw_text or "")
    parsed: Dict[str, Any] = {}
    parse_error: Optional[str] = None
    if js is None:
        parse_error = "no_json_found"
    else:
        try:
            obj = json.loads(js)
            if isinstance(obj, dict):
                parsed = obj
            else:
                parse_error = "json_not_dict"
        except Exception as e:
            parse_error = f"json_parse_error: {e}"

    rows: List[Dict[str, Any]] = []
    allowed_inc = {"not applicable", "not enough information", "included", "not included"}
    allowed_exc = {"not applicable", "not enough information", "excluded", "not excluded"}
    allowed = allowed_inc if inc_exc == "inclusion" else allowed_exc

    for i in range(n_criteria):
        k = str(i)
        v = parsed.get(k)
        reasoning = None
        sent_ids: List[int] = []
        label = "not enough information"

        if isinstance(v, list) and len(v) >= 3:
            if isinstance(v[0], str):
                reasoning = v[0]
            if isinstance(v[1], list):
                for x in v[1]:
                    try:
                        sent_ids.append(int(x))
                    except Exception:
                        continue
            if isinstance(v[2], str):
                cand = v[2].strip().lower()
                if cand in allowed:
                    label = cand

        rows.append({"criterion_id": i, "reasoning": reasoning, "sentence_ids": sent_ids, "label": label})

    return {"parse_error": parse_error, "raw_text": raw_text, "rows": rows}


def _trialgpt_side_sat_like(side_rows: List[Dict[str, Any]], inc_exc: str) -> Tuple[Optional[bool], Dict[str, Any]]:
    labels = [r.get("label") for r in side_rows]
    unknown: List[int] = []

    if inc_exc == "inclusion":
        violated: List[int] = []
        for r in side_rows:
            if r.get("label") == "not included":
                violated.append(int(r.get("criterion_id", -1)))
            elif r.get("label") == "not enough information":
                unknown.append(int(r.get("criterion_id", -1)))

        if violated:
            return False, {"violated": violated, "unknown": unknown}
        if labels and all(l in ("included", "not applicable") for l in labels):
            return True, {"violated": violated, "unknown": unknown}
        return None, {"violated": violated, "unknown": unknown}

    triggered: List[int] = []
    for r in side_rows:
        if r.get("label") == "excluded":
            triggered.append(int(r.get("criterion_id", -1)))
        elif r.get("label") == "not enough information":
            unknown.append(int(r.get("criterion_id", -1)))

    if triggered:
        return False, {"triggered": triggered, "unknown": unknown}
    if labels and all(l in ("not excluded", "not applicable") for l in labels):
        return True, {"triggered": triggered, "unknown": unknown}
    return None, {"triggered": triggered, "unknown": unknown}


def _mbench_write_trialgpt(patient_id: str, parent_id: str, inc_exc: str, payload: Dict[str, Any]) -> None:
    try:
        root = pathlib.Path("mbench") / "trialgpt" / patient_id / parent_id
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{inc_exc}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
        )
    except Exception:
        pass


def _load_trial_from_corpus_parent_only(parent_id: str, cfg: Config) -> Optional[Dict[str, Any]]:
    for cp in _corpus_jsonl_paths(cfg):
        found = _load_trial_from_corpus_jsonl(parent_id, cp)
        if found is not None:
            return found
    return None


def _trialgpt_side_fingerprint(
    *,
    inc_exc: str,
    system_prompt: str,
    user_prompt: str,
    criteria_list: List[str],
    model_name: str,
    temperature: Optional[float],
    parent_id: str,
    patient_id: str,
) -> str:
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "stage": f"trialgpt_{inc_exc}",
        "inc_exc": inc_exc,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "criteria_list": criteria_list,
        "model_name": model_name,
        "temperature": temperature,
        "trial_id_parent": parent_id,
        "patient_id": patient_id,
    }
    return _stable_hash(payload)


def run_trialgpt_criterion_judge(
    trial_id: str,
    patient: Dict[str, Any],
    cfg: Config,
    engine: AzureInferenceEngine,
    *,
    pair_dir: pathlib.Path,
    out_root: pathlib.Path,
    temperature: Optional[float] = None,
    force: bool = False,
) -> Dict[str, Any]:
    parent_id = parent_nct_id(trial_id)
    judge_path = pair_dir / f"{parent_id}__trialgpt_judge.json"

    if judge_path.exists() and not force:
        try:
            existing = json.loads(judge_path.read_text(encoding="utf-8"))
        except Exception:
            existing = None
    else:
        existing = None

    try:
        trial_obj = _load_trial_from_corpus_parent_only(parent_id, cfg)
        if trial_obj is None:
            trial_obj = load_trial_record(parent_id, cfg)

        patient_pkg = _trialgpt_patient_with_sentence_ids(patient)
        patient_sentenced = patient_pkg["patient_note_sentenced"]
        patient_id = str(patient.get("patient_id") or patient.get("_id") or patient.get("id") or "UNKNOWN")

        sides_out: Dict[str, Any] = {}
        side_meta: Dict[str, Any] = {}

        for inc_exc in ("inclusion", "exclusion"):
            sys_p, usr_p, criteria_list = _trialgpt_prompts(trial_obj, inc_exc, patient_sentenced)
            fingerprint = _trialgpt_side_fingerprint(
                inc_exc=inc_exc,
                system_prompt=sys_p,
                user_prompt=usr_p,
                criteria_list=criteria_list,
                model_name=cfg.model_name,
                temperature=temperature,
                parent_id=parent_id,
                patient_id=patient_id,
            )

            cache_path = _cache_file_for_fingerprint(out_root, f"trialgpt_{inc_exc}", fingerprint)
            side_payload = None if force else _safe_read_json(cache_path)
            if _cache_payload_matches(side_payload, fingerprint):
                payload = side_payload.get("payload")
                if isinstance(payload, dict):
                    sides_out[inc_exc] = payload
                    side_meta[inc_exc] = {
                        "fingerprint": fingerprint,
                        "cache_path": str(cache_path),
                        "used_cache": True,
                    }
                    _mbench_write_trialgpt(patient_id, parent_id, inc_exc, payload)
                    continue

            t0 = time.perf_counter()
            raw = _call_engine_chat(
                engine,
                sys_p,
                usr_p,
                temperature=temperature,
                __trial_id=parent_id,
                __side=inc_exc,
                __stage="trialgpt",
                __cohort_id=patient_id,
            )
            dur = time.perf_counter() - t0

            parsed = _trialgpt_parse_output(raw, n_criteria=len(criteria_list), inc_exc=inc_exc)
            payload = {
                "cache": {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "stage": f"trialgpt_{inc_exc}",
                    "fingerprint": fingerprint,
                    "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "model_name": cfg.model_name,
                    "temperature": temperature,
                    "cache_path": str(cache_path),
                },
                "inc_exc": inc_exc,
                "criteria": criteria_list,
                "duration_s": dur,
                "temperature_used": temperature,
                "parse_error": parsed.get("parse_error"),
                "rows": parsed.get("rows"),
                "raw_text": parsed.get("raw_text"),
            }
            _safe_write_json_atomic(cache_path, {"cache": payload["cache"], "payload": payload})
            sides_out[inc_exc] = payload
            side_meta[inc_exc] = {
                "fingerprint": fingerprint,
                "cache_path": str(cache_path),
                "used_cache": False,
            }
            _mbench_write_trialgpt(patient_id, parent_id, inc_exc, payload)

        inc_sat, inc_detail = _trialgpt_side_sat_like(sides_out["inclusion"]["rows"], "inclusion")
        exc_sat, exc_detail = _trialgpt_side_sat_like(sides_out["exclusion"]["rows"], "exclusion")

        eligible_strict = None if (inc_sat is None or exc_sat is None) else (bool(inc_sat) and bool(exc_sat))
        eligible = (inc_sat is not False) and (exc_sat is not False)

        payload = {
            "cache": {
                "schema_version": CACHE_SCHEMA_VERSION,
                "stage": "trialgpt_combined",
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "side_meta": side_meta,
            },
            "trial_id_original": trial_id,
            "trial_id_parent": parent_id,
            "patient_id": patient_id,
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "patient_sentences": patient_pkg.get("sentences"),
            "inclusion": sides_out.get("inclusion"),
            "exclusion": sides_out.get("exclusion"),
            "aggregate": {
                "inclusion_sat_like": inc_sat,
                "exclusion_sat_like": exc_sat,
                "eligible": eligible,
                "eligible_strict": eligible_strict,
                "inclusion_detail": inc_detail,
                "exclusion_detail": exc_detail,
            },
        }

        _safe_write_json_atomic(judge_path, payload)
        return payload

    except Exception as e:
        err_payload = {
            "trial_id_original": trial_id,
            "trial_id_parent": parent_id,
            "patient_id": patient.get("patient_id") or patient.get("_id") or patient.get("id"),
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "error": f"{type(e).__name__}: {e}",
        }
        _safe_write_json_atomic(judge_path, err_payload)
        return err_payload


def run_single_pair(
    trial_id: str,
    patient_id: str,
    cfg: Config,
    engine: AzureInferenceEngine,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    patient = load_patient(patient_id, cfg.data_root, args.patient_file)

    labels_obj: Optional[Dict[str, Any]] = None
    if getattr(args, "labels", None):
        for p in args.labels:
            cand = pathlib.Path(p)
            if cand.is_file():
                try:
                    labels_obj = json.loads(cand.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

    side_results: Dict[str, Any] = {}
    for side in SIDES:
        side_results[side] = run_match_for_side(
            side,
            trial_id,
            patient,
            cfg,
            engine,
            labels_obj,
            args,
        )

    inc = side_results["inclusion"]["sat_like"]
    exc = side_results["exclusion"]["sat_like"]

    eligible_strict = None if (inc is None or exc is None) else (bool(inc) and bool(exc))
    eligible = (inc is not False) and (exc is not False)

    # Threshold-ablation: verdict computed on the same mine with the
    # EVAL_USE_RICH_PATIENT_VALUES flag flipped (None if dual eval disabled).
    inc_abl = side_results["inclusion"].get("sat_like_ablation")
    exc_abl = side_results["exclusion"].get("sat_like_ablation")
    eligible_ablation = None
    if inc_abl is not None or exc_abl is not None:
        eligible_ablation = (inc_abl is not False) and (exc_abl is not False)

    timestamp = dt.datetime.now().isoformat(timespec="seconds")

    out_root = pathlib.Path(args.out_root).resolve()
    pair_dir = out_root / patient_id
    pair_dir.mkdir(parents=True, exist_ok=True)

    def _dump(path: pathlib.Path, obj: Any):
        path.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

    inc_summary = side_results["inclusion"]["summary"] or {}
    exc_summary = side_results["exclusion"]["summary"] or {}

    overall = {
        "trial_id": trial_id,
        "trial_id_parent": parent_nct_id(trial_id),
        "patient_id": patient_id,
        "timestamp": timestamp,
        "eligible": eligible,
        "eligible_strict": eligible_strict,
        "inclusion_sat_like": inc,
        "exclusion_sat_like": exc,
        "inclusion_status": inc_summary.get("status"),
        "exclusion_status": exc_summary.get("status"),
        "inclusion_unsat_assertions": inc_summary.get("unsat_assertions", []),
        "exclusion_unsat_assertions": exc_summary.get("unsat_assertions", []),
        "var_miner_mode": cfg.miner_mode,
        "var_miner_conservative": bool(cfg.conservative),
        "inclusion_cache": side_results["inclusion"].get("cache"),
        "exclusion_cache": side_results["exclusion"].get("cache"),
    }

    out: Dict[str, Any] = {
        "trial_id": trial_id,
        "trial_id_parent": parent_nct_id(trial_id),
        "patient_id": patient_id,
        "timestamp": timestamp,
        "eligible": eligible,
        "eligible_strict": eligible_strict,
        "eligible_ablation": eligible_ablation,
        "inclusion_sat_like_ablation": inc_abl,
        "exclusion_sat_like_ablation": exc_abl,
        "inclusion": side_results["inclusion"],
        "exclusion": side_results["exclusion"],
        "llm_judge": None,
        "trialgpt_judge": None,
    }

    _dump(pair_dir / f"{trial_id}__overall.json", overall)
    _dump(pair_dir / f"{trial_id}__inclusion_stats.json", side_results["inclusion"]["summary"])
    _dump(pair_dir / f"{trial_id}__exclusion_stats.json", side_results["exclusion"]["summary"])
    _dump(pair_dir / f"{trial_id}__full.json", out)

    if getattr(args, "llm_judge", False):
        judge_id = parent_nct_id(trial_id)
        llm_path = pair_dir / f"{judge_id}__llm_judge.json"
        llm_payload: Dict[str, Any]

        try:
            prompt_path = cfg.eligibility_prompt_path(getattr(args, "llm_judge_prompt", None))
            llm_payload = run_llm_eligibility_judge(
                trial_id=trial_id,
                patient=patient,
                cfg=cfg,
                engine=engine,
                prompt_path=prompt_path,
                out_root=out_root,
                temperature=float(getattr(args, "llm_judge_temperature", 0.0) or 0.0),
            )

            if getattr(args, "llm_judge_force", False):
                # force means recompute above; otherwise fingerprint cache already handles reuse
                pass

        except Exception as e:
            llm_payload = {
                "trial_id_original": trial_id,
                "trial_id_parent": judge_id,
                "patient_id": patient_id,
                "error": f"{type(e).__name__}: {e}",
            }

        _dump(llm_path, llm_payload)

        out["llm_judge"] = llm_payload
        overall["llm_judge_trial_id"] = judge_id

        if isinstance(llm_payload, dict) and isinstance(llm_payload.get("result"), dict):
            res = llm_payload["result"]
            overall["llm_eligible"] = res.get("eligible")
            overall["llm_eligibility"] = res.get("eligibility")
            overall["llm_reasoning"] = res.get("reasoning")
            overall["llm_explanation"] = res.get("explanation")
            overall["llm_parse_error"] = res.get("parse_error")
            overall["llm_cache"] = llm_payload.get("cache")
        elif isinstance(llm_payload, dict) and "error" in llm_payload:
            overall["llm_error"] = llm_payload.get("error")

        _dump(pair_dir / f"{trial_id}__overall.json", overall)
        _dump(pair_dir / f"{trial_id}__full.json", out)

    if getattr(args, "trialgpt_judge", False):
        parent_id = parent_nct_id(trial_id)
        try:
            temp_override = getattr(args, "trialgpt_judge_temperature", None)
            temperature: Optional[float]
            if temp_override is None:
                temperature = None
            else:
                try:
                    temperature = float(temp_override)
                except Exception:
                    temperature = None

            tg_payload = run_trialgpt_criterion_judge(
                trial_id=trial_id,
                patient=patient,
                cfg=cfg,
                engine=engine,
                pair_dir=pair_dir,
                out_root=out_root,
                temperature=temperature,
                force=bool(getattr(args, "trialgpt_judge_force", False)),
            )
        except Exception as e:
            tg_payload = {
                "trial_id_original": trial_id,
                "trial_id_parent": parent_id,
                "patient_id": patient_id,
                "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                "error": f"{type(e).__name__}: {e}",
            }

        out["trialgpt_judge"] = tg_payload
        overall["trialgpt_trial_id"] = parent_id

        if isinstance(tg_payload, dict) and isinstance(tg_payload.get("aggregate"), dict):
            agg = tg_payload["aggregate"]
            overall["trialgpt_eligible"] = agg.get("eligible")
            overall["trialgpt_eligible_strict"] = agg.get("eligible_strict")
            overall["trialgpt_inclusion_sat_like"] = agg.get("inclusion_sat_like")
            overall["trialgpt_exclusion_sat_like"] = agg.get("exclusion_sat_like")
            overall["trialgpt_cache"] = tg_payload.get("cache")
        elif isinstance(tg_payload, dict) and "error" in tg_payload:
            overall["trialgpt_error"] = tg_payload.get("error")

        _dump(pair_dir / f"{trial_id}__overall.json", overall)
        _dump(pair_dir / f"{trial_id}__full.json", out)

    return out


def main(argv: List[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Whole-program SMT trial matcher (inclusion + exclusion) + optional LLM eligibility judge."
    )
    ap.add_argument("trial_id", help="NCT id, e.g., NCT02509286 (or subcohort like NCT00971386b)")
    ap.add_argument("patient_id", help="sigir-20141 | trec-20211 | trec-20221 | P001 ...")
    ap.add_argument("--data-root", default="../dataset/clinical_trial")
    ap.add_argument("--patient-file", default=None)
    ap.add_argument("--build-root", default="../build")
    ap.add_argument("--project-root", default="..")
    ap.add_argument("--prompt-root", default="prompts/clinical_trial")
    ap.add_argument("--prompt-map", default=None)

    ap.add_argument(
        "--miner-mode",
        choices=["infer", "explicit"],
        default="infer",
        help="Choose miner prompt mode for BOTH inclusion and exclusion (default: infer).",
    )

    ap.add_argument(
        "--conservative",
        action="store_true",
        help=(
            "Best-effort: use SMTVariableValueMinerConservative.<mode>.prompt for BOTH inclusion/exclusion "
            "if present; otherwise fall back to non-conservative."
        ),
    )

    ap.add_argument("--canonical-only", action="store_true")
    ap.add_argument("--canonical-json", default=None)
    ap.add_argument("--vv-batch-size", type=int, default=10)
    ap.add_argument("--verbose", action="store_true")

    ap.add_argument("--labels", action="append", default=None)
    ap.add_argument("--log-json", default=None)

    ap.add_argument("--out-root", default="match_out", help="Base folder for per-pair outputs (default: match_out)")

    ap.add_argument("--llm-judge", action="store_true", help="Run LLM eligibility judgement per pair")
    ap.add_argument("--llm-judge-prompt", default=None)
    ap.add_argument("--llm-judge-temperature", type=float, default=0.0)
    ap.add_argument("--llm-judge-force", action="store_true", help="Re-run judge even if cached JSON exists")

    ap.add_argument("--trialgpt-judge", action="store_true", help="Run TrialGPT-style criterion matching per pair (parent-cached)")
    ap.add_argument(
        "--trialgpt-judge-temperature",
        default=None,
        help="Optional temperature override for TrialGPT judge. If omitted, uses inference engine defaults.",
    )
    ap.add_argument("--trialgpt-judge-force", action="store_true", help="Re-run TrialGPT judge even if cached JSON exists")

    args = ap.parse_args(argv)

    cfg = Config(
        data_root=pathlib.Path(args.data_root).resolve(),
        build_root=pathlib.Path(args.build_root).resolve(),
        project_root=pathlib.Path(args.project_root).resolve(),
        prompt_root=pathlib.Path(args.prompt_root),
        prompt_map_json=pathlib.Path(args.prompt_map) if args.prompt_map else None,
        conservative=bool(getattr(args, "conservative", False)),
        miner_mode=str(getattr(args, "miner_mode", "infer") or "infer"),
    )
    engine = AzureInferenceEngine(
        endpoint=cfg.azure_endpoint,
        api_key_env_var="OPENAI_API_KEY",
        model_name=cfg.model_name,
    )

    try:
        out = run_single_pair(
            trial_id=args.trial_id,
            patient_id=args.patient_id,
            cfg=cfg,
            engine=engine,
            args=args,
        )
    except FileNotFoundError as e:
        print(
            json.dumps(
                {
                    "trial_id": args.trial_id,
                    "patient_id": args.patient_id,
                    "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                    "error": str(e),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    except Exception as e:
        import traceback as _tb
        _tb.print_exc(file=sys.stderr)
        print(
            json.dumps(
                {
                    "trial_id": args.trial_id,
                    "patient_id": args.patient_id,
                    "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                    "error": f"{type(e).__name__}: {e}",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    print(json.dumps(out, ensure_ascii=False, indent=2, default=_json_default))

    if args.log_json:
        p = pathlib.Path(args.log_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()