P4 Mirror Ladder App v3.1 Rule Library Path Fix

This build fixes the v3.0 deployment issue where the C120 preflight could not find core_rule_library_stable_only_filtered.csv.

Changes:
- Includes the rule library in BOTH:
  - /rules/core_rule_library_stable_only_filtered.csv
  - /core_rule_library_stable_only_filtered.csv
- App now searches supported rule-library locations before failing:
  - /rules/
  - repo root
  - /data/
  - /profiles/
  - /IN/
- The C120 status table now reports the actual path being used.

The C120 rule library was integrated in v3.0, but the deployed app in the screenshot could not see the nested /rules/ file. This build makes the lookup deployment-safe.
