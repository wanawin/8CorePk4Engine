# P4 Mirror Ladder App v3.5 C120 Dynamic Test Ready — Profile Bundle Deployment Fix

This build fixes repeated Streamlit/GitHub missing-profile failures by adding a compressed scoring-profile bundle fallback.

Visible version marker: v3.5.

Required behavior:
- Step 0: all seed structures allowed; AZ/MD optional exclusion.
- Step 1: FULL120 / 360 AABC member enumeration only.
- Step 2: corrected mirror-bucket refinement.
- C120 preflight: rebuilds dynamic C120 matrix from current history and stable rule library.
- Step 3: watched-8 historical score-driven core selection.
- Step 4: one global best-play ranking; cuts lowest-ranked rows first only.
- Step 5: export/display only, no reduction.

Deployment fix:
- Required V6 profile CSVs can be loose files under profiles/ or root.
- If loose files are missing, the app reads them from profiles_required_bundle.zip.
- The large StreamRank profile is intentionally bundled instead of loose to avoid GitHub browser upload problems.

Upload the full unzipped folder contents. You do not need RUN_APP.bat or __pycache__.
