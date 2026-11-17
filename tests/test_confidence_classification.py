import pytest
from app.gaming_radar import _classify_confidence

CASES = [
    # 1. Rumores que mencionam Rockstar ou Take-Two (devem ser RUMOR, não oficial)
    ("Rumor: suposto vazamento da Rockstar revela mapa de GTA 6", "Twitter", "Rumor / Não verificado"),
    ("Suposta data de lançamento de GTA 6 vaza em fórum", "Reddit", "Rumor / Não verificado"),
    ("GTA 6 pode ter adiamento segundo novo rumor", "MeuPlayStation", "Rumor / Não verificado"),
    ("Insider diz que Rockstar teria cancelado versão de PC de GTA 6", "Voxel", "Rumor / Não verificado"),
    ("Possível leak mostra trailer 2 de GTA 6 gravado com celular", "YouTube", "Rumor / Não verificado"),
    ("Especulação sobre preço de GTA 6 agita redes sociais", "TecMundo", "Rumor / Não verificado"),
    ("Novo mapa de GTA 6 não confirmado surge na internet", "Reddit", "Rumor / Não verificado"),
    ("GTA 6 teria 3 protagonistas segundo rumor antigo", "IGN", "Rumor / Não verificado"),

    # 2. Oficiais / Confirmados (Rockstar direta ou confirmação sem termos de dúvida)
    ("Rockstar Games divulga Trailer 2 de GTA 6 com data final", "Rockstar Games", "Oficial / Confirmado"),
    ("Comunicado oficial: Rockstar Games atualiza status de GTA 6", "Rockstar Newswire", "Oficial / Confirmado"),
    ("Take-Two confirma janela de lançamento de GTA 6 no outono de 2025", "Take-Two Interactive", "Oficial / Confirmado"),
    ("Trailer oficial de GTA 6 bate recorde mundial", "Rockstar Games", "Oficial / Confirmado"),
    ("Rockstar confirma oficialmente plataformas para GTA 6", "Rockstar Games", "Oficial / Confirmado"),

    # 3. Notícia / Imprensa (Veículos jornalísticos cobrindo fatos sem rumor)
    ("Bloomberg analisa receita esperada de GTA 6 no primeiro ano", "Bloomberg", "Notícia / Imprensa"),
    ("IGN lista tudo o que sabemos sobre a protagonista Lucia em GTA 6", "IGN", "Notícia / Imprensa"),
    ("Entrevista com elenco de GTA 6 detalha bastidores", "Eurogamer", "Notícia / Imprensa"),
    ("PlayStation Blog destaca avanços gráficos da engine RAGE em GTA 6", "PlayStation Blog", "Notícia / Imprensa"),
    ("Comparativo técnico das cidades de Leonida e Vice City", "Digital Foundry", "Notícia / Imprensa"),

    # 4. Análise / Tendência / Opinião
    ("Porque GTA 6 vai mudar a indústria dos games para sempre", "Canal Gamer", "Análise / Tendência"),
    ("5 coisas que os fãs esperam encontrar em Vice City", "Blog Geral", "Análise / Tendência"),
]

@pytest.mark.parametrize("title,source,expected", CASES)
def test_classify_confidence_cases(title, source, expected):
    result = _classify_confidence(title=title, source=source)
    assert result == expected, f"Falha para '{title}' ({source}): esperado '{expected}', obteve '{result}'"
