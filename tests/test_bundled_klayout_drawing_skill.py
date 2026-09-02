from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "klayout-drawing"


def test_klayout_drawing_skill_is_self_contained() -> None:
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "name: klayout-drawing" in skill_text
    assert "C:\\Users\\" not in skill_text
    assert ".codex/skills" not in skill_text

    relative_links = re.findall(r"\]\(([^)]+)\)", skill_text)
    project_links = [link for link in relative_links if not link.startswith(("http://", "https://"))]
    assert project_links
    for link in project_links:
        assert (SKILL_ROOT / link).resolve().is_file(), link


def test_klayout_drawing_skill_bundles_executable_helpers() -> None:
    expected = {
        "assets/python_pcell_library.py",
        "references/geometry.md",
        "references/pcells.md",
        "references/project-integration.md",
        "references/verification.md",
        "scripts/inspect_layout.py",
        "scripts/render_layout.py",
        "scripts/run_klayout.py",
        "scripts/smoke_test.py",
    }
    assert expected <= {
        path.relative_to(SKILL_ROOT).as_posix()
        for path in SKILL_ROOT.rglob("*")
        if path.is_file()
    }
