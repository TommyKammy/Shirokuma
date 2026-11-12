"""Production body classic-script sources for Workbench UI test harnesses."""
from html.parser import HTMLParser
from pathlib import Path
import re


WORKBENCH = Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench'
INDEX = WORKBENCH / 'index.html'
SCRIPT_ASSETS = {'/assets/result_chart.js': 'result_chart.js', '/assets/base_i18n.js': 'base_i18n.js'}
STYLE_ASSETS = {'/assets/workbench.css': 'workbench.css'}
SCRIPT_BOUNDARY = '\n// --- Workbench script boundary ---\n'


class WorkbenchSource(HTMLParser):
    """Load body scripts in document order, excluding early head bootstrap."""

    def __init__(self, html=None, asset_root=WORKBENCH):
        super().__init__()
        self.asset_root = Path(asset_root)
        self.sources = []
        self.style_sources = []
        self.style = None
        self.in_body = False
        self.script = None
        self.feed(INDEX.read_text() if html is None else html)
        self.close()

    @property
    def source(self):
        """Compatibility source retaining explicit file boundaries for extraction."""
        return SCRIPT_BOUNDARY.join(self.sources)

    @property
    def css(self):
        """Inline and fixed stylesheet texts in document order."""
        return '\n'.join(self.style_sources)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'style':
            self.style = []
        if tag == 'link' and 'stylesheet' in attrs.get('rel', '').lower().split():
            reference = attrs.get('href', '')
            if reference not in STYLE_ASSETS:
                raise ValueError('Unsupported Workbench stylesheet reference: ' + reference)
            self.style_sources.append((self.asset_root / STYLE_ASSETS[reference]).read_text())
        if tag == 'body':
            self.in_body = True
        if tag != 'script' or not self.in_body:
            return
        if attrs.get('type', '').strip().lower() not in ('', 'text/javascript', 'application/javascript'):
            return
        if 'src' in attrs:
            if attrs['src'] not in SCRIPT_ASSETS:
                raise ValueError('Unsupported Workbench script reference: ' + attrs['src'])
            self.sources.append((self.asset_root / SCRIPT_ASSETS[attrs['src']]).read_text())
        else:
            self.script = []

    def handle_data(self, data):
        if self.style is not None:
            self.style.append(data)
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == 'style' and self.style is not None:
            self.style_sources.append(''.join(self.style))
            self.style = None
        if tag == 'script' and self.script is not None:
            self.sources.append(''.join(self.script))
            self.script = None
        if tag == 'body':
            self.in_body = False


def function(name, source=None):
    """Slice within one script, accepting ordered parts or compatibility source."""
    parts = WorkbenchSource().sources if source is None else (
        source.split(SCRIPT_BOUNDARY) if isinstance(source, str) else source)
    source = next((part for part in parts if 'function ' + name + '(' in part), '')
    start = source.index('function ' + name + '(')
    if source[max(0, start - 6):start] == 'async ':
        start -= 6
    end = re.search(r'\n(?:async )?function ', source[start + 1:])
    declaration = source[start:start + 1 + end.start()] if end else source[start:]
    return declaration.split("\n$('", 1)[0]
