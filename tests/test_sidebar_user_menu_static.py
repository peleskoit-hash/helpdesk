from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "api" / "app" / "templates"
SIDEBAR_JS = ROOT / "api" / "app" / "static" / "sidebar.js"
STYLES_CSS = ROOT / "api" / "app" / "static" / "styles.css"


def test_sidebar_templates_keep_logout_form_for_menu_submit() -> None:
    templates = sorted(TEMPLATES_DIR.glob("*.html"))
    sidebar_templates = []
    for template in templates:
        content = template.read_text(encoding="utf-8")
        if "sidebar-user-actions" not in content:
            continue
        sidebar_templates.append(template.name)
        assert 'class="agent-avatar"' in content
        assert 'form action="/logout" method="post"' in content
        assert 'data-sidebar-logout-form hidden' in content
        assert "sidebar-logout-btn" not in content

    assert sidebar_templates


def test_sidebar_user_menu_static_contract() -> None:
    sidebar_js = SIDEBAR_JS.read_text(encoding="utf-8")
    styles_css = STYLES_CSS.read_text(encoding="utf-8")

    assert "function ensureUserMenuStructure()" in sidebar_js
    assert "sidebarUserMenuToggle" in sidebar_js
    assert "\\u041b\\u0438\\u0447\\u043d\\u044b\\u0435 \\u0434\\u0430\\u043d\\u043d\\u044b\\u0435" in sidebar_js
    assert "\\u0423\\u0432\\u0435\\u0434\\u043e\\u043c\\u043b\\u0435\\u043d\\u0438\\u044f \\u0432 \\u0431\\u043e\\u0442\\u0435" in sidebar_js
    assert "\\u041e\\u0442\\u0437\\u044b\\u0432\\u044b" in sidebar_js
    assert "logoutForm.requestSubmit();" in sidebar_js
    assert 'document.addEventListener("pointerdown"' in sidebar_js

    assert ".sidebar-user-menu {" in styles_css
    assert ".sidebar-user-menu[hidden] {" in styles_css
    assert ".sidebar-user-panel {" in styles_css
    assert ".sidebar-user-panel.menu-open .agent-avatar" in styles_css
    assert ".sidebar-logout-btn {" in styles_css
    assert "display: none;" in styles_css


def test_sidebar_template_entrypoints_use_versioned_sidebar_bundle() -> None:
    templates = sorted(TEMPLATES_DIR.glob("*.html"))
    sidebar_entrypoints = []
    for template in templates:
        content = template.read_text(encoding="utf-8")
        if "/static/sidebar.js" not in content:
            continue
        sidebar_entrypoints.append(template.name)
        assert '/static/sidebar.js?v=20260903a' in content
        assert '<script src="/static/sidebar.js"></script>' not in content

    assert sidebar_entrypoints
