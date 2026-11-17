import pytest
from app.gaming_radar import ET

def test_defusedxml_loaded():
    """Confirma que defusedxml é utilizado no lugar de xml.etree não protegido."""
    assert "defusedxml" in ET.__name__

def test_defusedxml_parses_valid_rss():
    sample_rss = """<rss version="2.0">
        <channel>
            <title>GTA 6 News</title>
            <item>
                <title>Trailer 2 Anunciado</title>
                <link>https://example.com/trailer2</link>
                <description>Detalhes do trailer 2</description>
            </item>
        </channel>
    </rss>"""
    root = ET.fromstring(sample_rss)
    items = root.findall(".//item")
    assert len(items) == 1
    assert items[0].find("title").text == "Trailer 2 Anunciado"
