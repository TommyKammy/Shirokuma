from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preflight_supervisor_issues import extract_references  # noqa: E402


class ExtractReferencesTests(unittest.TestCase):
    def test_extracts_references_from_repository_authored_h2_section(self) -> None:
        body = """\
## Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`

## Risk and rollback

- Revert the pull request.
"""

        self.assertEqual(
            extract_references(body),
            ["docs/design/01_Product/010_Project_Charter.md"],
        )

    def test_extracts_references_from_github_issue_form_h3_section(self) -> None:
        body = """\
### Related docs / ADR

- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/07_ADR/ADR-0015_Use_AGENTS_md_as_mandatory_repository_instruction.md`

### Risk tier

T0 - docs or metadata only
"""

        self.assertEqual(
            extract_references(body),
            [
                "docs/design/04_Development/044_Issue_and_PR_Workflow.md",
                "docs/design/07_ADR/ADR-0015_Use_AGENTS_md_as_mandatory_repository_instruction.md",
            ],
        )

    def test_rejects_non_contract_heading_levels(self) -> None:
        body = """\
#### Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`
"""

        self.assertEqual(extract_references(body), [])


if __name__ == "__main__":
    unittest.main()
