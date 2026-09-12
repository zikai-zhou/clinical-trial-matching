# Authors

This release was squashed from a working repository, so its git history shows
only the person who assembled it. That history is not the record of who wrote
this software. The list below is, and it is drawn from the development
repository's actual commit log.

| | Contribution | Development-repo commits |
|---|---|---|
| **Zikai (Cyrus) Zhou** | System design, the SMT matcher, MaxSMT accountability artifacts, evaluation | ~903 (Apr 2025 – May 2026) |
| **Yilin (Clark) Xu** | Entity reports, the patient compiler, the trial-side constraint categorizer | 291 (Sep 2025 – Feb 2026) |
| **Yufei Jin** | mbench, patient-procedure build passes, the review frontend | ~190 (Sep – Nov 2025) |
| **Daniel Wu** | Contributions to the development repository | 5 |

Components in this release that came substantially from that work include
`patient_compiler/` (96 files), `trial_compiler/constraint_categorizer/`, and
the mbench instrumentation inside `verdict/engine/`.

Commit counts measure activity, not importance, and are given only to show
that every person above is a substantial contributor rather than an
acknowledgement. Identities were normalised across the several
name/email pairs each person committed under.

Developed at the Stanford Open Virtual Assistant Lab (OVAL) in collaboration
with Mayo Clinic. See `NOTICE`.
