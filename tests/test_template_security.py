import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "absulli" / "web" / "templates"
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def test_templates_have_no_inline_event_handlers():
    offenders = []
    for template in sorted(TEMPLATES_DIR.rglob("*.html")):
        for line_number, line in enumerate(template.read_text().splitlines(), start=1):
            if INLINE_HANDLER.search(line):
                offenders.append(f"{template.relative_to(TEMPLATES_DIR)}:{line_number}")
    assert offenders == []


def test_history_page_size_select_uses_auto_submit():
    partial = (TEMPLATES_DIR / "partials" / "history_page_size.html").read_text()
    assert "data-auto-submit" in partial
    assert "onchange" not in partial
