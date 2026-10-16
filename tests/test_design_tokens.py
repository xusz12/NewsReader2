"""Prevent the supplied design baseline, exported assets and component tokens drifting apart."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def declarations(css):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    return {key: ' '.join(value.split()) for key, value in re.findall(r'(--[\w-]+):\s*([^;]+);', css)}


def test_exported_assets_match_corrected_design_handoff():
    spec = (ROOT / 'DESIGN_SPEC_AND_TOKENS_Fix.md').read_text().split('### 8.1 完整 CSS 变量表')[1]
    baseline = re.search(r'```css\n(.*?)\n```', spec, re.S).group(1)
    css = (ROOT / 'static/tokens.css').read_text()
    # Compare every declaration in each theme, including derived colors and shadows.
    for selector in [':root', '[data-theme="dark"]', '[data-theme="eink"]']:
        block = lambda text: re.search(re.escape(selector) + r'\s*\{(.*?)\}', text, re.S).group(1)
        assert declarations(block(css)) == declarations(block(baseline))
    exported = json.loads((ROOT / 'static/design-tokens.json').read_text())
    assert exported == json.loads(re.search(r'```json\n(.*?)\n```', spec, re.S).group(1))


def test_components_resolve_tokens_without_redefining_baseline():
    css = (ROOT / 'static/style.css').read_text()
    tokens = (ROOT / 'static/tokens.css').read_text()
    defined = declarations(tokens) | declarations(css)
    assert set(re.findall(r'var\((--[\w-]+)', css + tokens)) <= defined.keys()
    # E-ink may specialize status and derived colors; layout, scale and shadows stay canonical.
    for name in ['header-height', 'sidebar-width', 'feed-width', 'fs-h1', 'fs-h2', 'fs-h3', 'fs-body', 'fs-small', 'fs-meta', 'radius', 'radius-sm', 'radius-lg', 'shadow-subtle', 'shadow-float']:
        assert not re.search(r'--' + name + r'\s*:', css)
    html = (ROOT / 'static/index.html').read_text()
    assert html.index('/static/tokens.css') < html.index('/static/style.css')
