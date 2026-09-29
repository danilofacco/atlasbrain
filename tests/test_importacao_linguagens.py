import io
import socket
import pytest
from atlasbrain import codigo, config, importacao


@pytest.mark.parametrize('ext,text,name,call', [
    ('.ex', 'defmodule Demo do\n def hello(x), do: world(x)\nend', 'hello', 'world'),
    ('.jl', 'function hello(x)\n world(x)\nend', 'hello', 'world'),
    ('.r', 'hello <- function(x) { world(x) }', 'hello', 'world'),
    ('.pl', 'use Foo;\nsub hello { world(); }', 'hello', 'world'),
    ('.ps1', 'function Hello { World }', 'Hello', 'World'),
    ('.hs', 'module Demo where\nimport Data.List\nhello x = world x', 'hello', 'world'),
    ('.ml', 'let hello x = world x', 'hello', 'world'),
])
def test_language_symbols_and_calls(ext, text, name, call):
    e = codigo.extrair(text, ext)
    assert e is not None
    assert [s[0] for s in e.simbolos if s[1] == 'funcao'] == [name]
    assert any(c[1] == call and c[0].endswith(name) for c in e.chamadas)
    assert not any(c[1] == name for c in e.chamadas)
    assert ext in config.CODE_EXT


def test_html_snapshot_provenance_duplicate_refresh(projeto, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return b'<title>Source</title><h1>Architecture</h1><p>Use SQLite</p><script>evil()</script>', 'text/html', 'utf-8', url
    monkeypatch.setattr(importacao, 'fetch', fetch)
    url = 'https://example.com/article'
    result = importacao.importar(projeto, url)
    note = projeto / result['path']
    text = note.read_text()
    assert 'source_url: https://example.com/article' in text
    assert 'Use SQLite' in text and 'evil()' not in text
    assert not importacao.importar(projeto, url)['importada']
    assert len(calls) == 1
    assert importacao.importar(projeto, url, atualizar=True)['path'] == result['path']


def test_pdf_snapshot_and_empty_scan(projeto, monkeypatch):
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    monkeypatch.setattr(importacao, 'fetch', lambda u: (buffer.getvalue(), 'application/pdf', 'utf-8', u))
    with pytest.raises(ValueError, match='OCR'):
        importacao.importar(projeto, 'https://example.com/scan.pdf')
    from pypdf._page import PageObject
    monkeypatch.setattr(PageObject, 'extract_text', lambda *a, **k: 'PDF architecture text')
    result = importacao.importar(projeto, 'https://example.com/text.pdf')
    assert 'PDF architecture text' in (projeto / result['path']).read_text()


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://127.0.0.1/', 'http://user:password@example.com/', 'http://[::1]/'])
def test_reject_unsafe_url(url):
    with pytest.raises(ValueError):
        importacao.validate_url(url)


def test_redirect_private_address_blocked():
    from urllib.request import Request
    with pytest.raises(ValueError):
        importacao.SafeRedirect().redirect_request(Request('https://example.com'), None, 302, '', {}, 'http://127.0.0.1/')
