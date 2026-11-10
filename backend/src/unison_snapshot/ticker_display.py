"""Expose unmatched ticker rows in the published renderer without frozen-input edits."""


def render(html: str) -> str:
    for css, body in (
        ("security", "${t.ticker}<small>${t.asset_name} · ${instrument(t)}</small>"),
        ("person-security", "<b>${t.ticker}</b><small>${t.asset_name}</small>"),
    ):
        original = ('<button class="' + css + ' ticker-link-button" '
                    'data-ticker-link="${t.ticker}">' + body + '</button>')
        unmatched = body.replace('${t.ticker}', '未匹配')
        replacement = ('${t.ticker ? `' + original + '` : `<span class="' + css +
                       '" data-ticker-status="unmatched">' + unmatched + '</span>`}')
        html = html.replace(original, replacement)
    return html.replace("t.ticker || '未映射代码'", "t.ticker || '未匹配'")
