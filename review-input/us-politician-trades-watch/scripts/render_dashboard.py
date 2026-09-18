#!/usr/bin/env python3
"""Render the canonical person-first disclosure dashboard as self-contained HTML."""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
from pathlib import Path

from process_snapshot import build_snapshot


SKILL_ROOT = Path(__file__).resolve().parent.parent


def image_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def initials_data_uri(person: dict) -> str:
    words = str(person.get("display_name") or "?").split()
    initials = "".join(word[0] for word in words[:2]).upper() or "?"
    safe = html.escape(initials)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="360" height="440" viewBox="0 0 360 440">'
        '<rect width="360" height="440" fill="#ede9df"/>'
        '<circle cx="180" cy="170" r="86" fill="#d9d2c5"/>'
        '<rect x="70" y="275" width="220" height="145" rx="72" fill="#d9d2c5"/>'
        f'<text x="180" y="194" text-anchor="middle" fill="#98620f" font-size="64" font-family="Arial">{safe}</text>'
        '</svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def load_dashboard_data(path: Path) -> dict:
    data = build_snapshot(json.loads(path.read_text(encoding="utf-8")))

    for person in data.get("people", []):
        portrait_path = person.get("portrait_path")
        if not portrait_path:
            portrait_url = str(person.get("portrait_url") or "")
            person["portrait_data_uri"] = portrait_url if portrait_url.startswith("https://") else initials_data_uri(person)
            person["portrait_is_placeholder"] = not portrait_url.startswith("https://")
            continue
        resolved = (SKILL_ROOT / portrait_path).resolve()
        if not resolved.is_relative_to(SKILL_ROOT.resolve()):
            raise ValueError(f"Portrait escapes Skill root: {portrait_path}")
        if not resolved.is_file():
            raise FileNotFoundError(f"Portrait not found: {resolved}")
        person["portrait_data_uri"] = image_data_uri(resolved)
        person["portrait_is_placeholder"] = False
    return data


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>White House Stock Tracker · __PAGE_TITLE__</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f4f1e8;
    --panel: #ffffff;
    --panel-2: #faf7f0;
    --line: #d8d3c8;
    --line-strong: #b8b0a2;
    --text: #17212b;
    --muted: #65717a;
    --soft: #39454e;
    --signal: #a6630e;
    --buy: #087f5b;
    --sell: #c53f4d;
    --info: #4269ad;
    /* 语义色的浅色档：描边(edge)与底色(wash)都由上面的 --buy/--sell/--signal 调浅而来。
       独立成变量是因为这几处曾残留暗色主题的亮青绿、亮粉与暗橄榄，和亮色调色板不同源。 */
    --signal-edge: rgba(166,99,14,.34);
    --signal-wash: rgba(166,99,14,.10);
    /* 顶部停靠栏当前的高度。吸附后收矮，窗口区块的 scroll-margin-top 靠它跟着走——
       写死的话，吸附态下点 30/90 会把窗口标题顶到停靠栏背后去。 */
    --dock-h: 102px;
    --info-edge: rgba(66,105,173,.34);
    --buy-edge: rgba(8,127,91,.34);
    --sell-edge: rgba(197,63,77,.34);
    --buy-wash: rgba(8,127,91,.12);
    --sell-wash: rgba(197,63,77,.12);
    --display: Georgia, "Times New Roman", serif;
    --sans: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  }
  * { box-sizing: border-box; } html { background: var(--bg); } body { margin: 0; overflow-x: hidden; background: var(--bg); color: var(--text); font: 16px/1.6 var(--sans); } button { font: inherit; } button:focus-visible { outline: 2px solid var(--signal); outline-offset: 3px; } .shell { width: min(1500px, calc(100% - 48px)); margin: 0 auto; padding-bottom: 80px; }
  /* 顶部停靠栏：滚过页首之后整条吸附在视口顶端，并收成一条更矮的横条。
     浏览到下方表格时不必再滚回页首才能换窗口——这正是它吸附的原因。
     背景必须不透明：吸附时它盖在正文之上，透明或半透明会让下面的字透出来。
     它只吸附在 .shell 的盒子里，所以宽屏下停靠栏与页面同宽，不会横贯整个视口。 */
  .dock-sentinel { height: 1px; } .masthead { position: sticky; top: 0; z-index: 30; min-height: var(--dock-h); display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); gap: 24px; background: var(--bg); transition: box-shadow .18s ease; }
  /* 吸附态：收矮、加投影、下缘两角变圆，读起来是一块浮在正文之上的独立面板，
     而不是页首被截了一半。z-index: 30 低于人物页(75)、单股页(70)与抽屉(80/90)，
     所以那几层全屏视图打开时仍然完整盖住停靠栏。 */
  .masthead.is-stuck { min-height: var(--dock-h); padding: 8px 18px; border-bottom-color: transparent; border-radius: 0 0 12px 12px; background: var(--panel); box-shadow: 0 1px 2px rgba(120,104,80,.12), 0 16px 30px -18px rgba(120,104,80,.5); } .masthead.is-stuck .eyebrow { display: none; } .masthead.is-stuck .brand-mark { height: 26px; } .masthead.is-stuck .brand h1 { font-size: 21px; }
  /* 吸附态右侧压成一行：原本上下两行的 meta(演示标记 + 数据截止) 与 actions 并排，
     否则停靠栏会比它要承载的信息还高。数据截止留在页首那一版里，浏览中不需要它。 */
  .masthead.is-stuck .header-side { display: flex; align-items: center; gap: 16px; } .masthead.is-stuck .data-cutoff { display: none; }
  /* 停靠栏里的 30/90 是整页唯一的窗口跳转控件，也是吸附下来之后最该被点到的东西，
     所以它比其它 .segmented 更大更重，未选中项也保留清晰的边界与文字对比——
     原先那行浅灰小字在米色页面上几乎看不出是可以点的。 */
  .masthead .segmented { padding: 4px; border-color: var(--line-strong); box-shadow: 0 1px 2px rgba(120,104,80,.10); } .masthead .segmented button { padding: 11px 20px; border-radius: 6px; color: var(--soft); font-size: 13.5px; font-weight: 800; letter-spacing: .03em; } .masthead .segmented button:hover { color: var(--signal); background: var(--signal-wash); } .masthead .segmented button.active { color: #fff; background: var(--signal); box-shadow: 0 2px 6px -1px rgba(166,99,14,.5); }
  /* 吸附后停靠栏只剩 60px，窗口标题的 scroll-margin 跟着一起收——
     写死 102px 的话，点完锚点标题上方会空出一大块。 */
  body.dock-stuck { --dock-h: 60px; } .brand { display: flex; align-items: center; gap: 14px; } .brand-mark { width: 13px; height: 42px; background: var(--signal); } .eyebrow { color: var(--signal); font: 700 10px/1.2 var(--mono); letter-spacing: .16em; text-transform: uppercase; margin-bottom: 6px; } .brand h1 { margin: 0; font: 500 clamp(24px, 3vw, 39px)/.98 var(--display); letter-spacing: -.025em; } .header-side { display: grid; justify-items: end; gap: 10px; } .header-meta { display: flex; align-items: center; justify-content: flex-end; gap: 10px; } .data-cutoff { color: var(--muted); font: 650 9px/1 var(--mono); letter-spacing: .055em; white-space: nowrap; } .demo-badge { padding: 5px 7px; color: #85570f; border: 1px solid #d7bd83; background: #fff7df; font: 750 10px/1 var(--mono); letter-spacing: .08em; } .header-actions { display: flex; align-items: center; gap: 10px; } .segmented { display: inline-flex; padding: 4px; background: #fff; border: 1px solid var(--line); } .segmented button {
    border: 0; color: var(--muted); background: transparent; padding: 9px 14px; cursor: pointer; font: 700 11px/1 var(--mono); letter-spacing: .04em;
    /* 「30 天」里的空格是天然断点：停靠栏一挤，按钮就缩到 min-content 把标签折成两行，
       而折行的按钮恰恰是读者要点的那个控件。宽度不够时该让旁边的标题去省略号。 */
    white-space: nowrap;
  } .segmented button.active { color: #fff; background: var(--signal); } .section-head { display: flex; justify-content: space-between; align-items: end; gap: 20px; padding: 34px 0 15px; border-bottom: 1px solid var(--line); } .section-head h3 { margin: 0; font: 500 27px/1 var(--display); letter-spacing: -.02em; } .section-head p { margin: 0; color: var(--muted); font-size: 12px; }
  /* 3 列：卡宽约 456px。2 列时卡片宽到 690px 而高仅 141px，左侧标的与右侧金额之间
     空出约 300px 死区，整个人物区读起来像被拉长的横条——这是「丑」的主因之一。 */
  .priority-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 18px; align-items: stretch; }
  /* 圆角 + 暖色投影成面，不再用描边框：阴影取米色调而非黑色，与 --bg 的暖调同源。 */
  .priority-card {
    /* position: relative 同样不是浮层要用的：浮层挂在 chip 那一行上，定位祖先是 .flow-chips，
       不是这张卡——实测拿掉它，浮层几何逐像素不变。留着是为了让 :hover 的 z-index
       有个明确的定位上下文可依，也免得日后有元素想贴卡片边角时无处可依。 */
    position: relative; min-width: 0; min-height: 0; display: flex; flex-direction: column; padding: 13px 15px 14px;
    border: 0; border-radius: 10px; color: inherit; background: var(--panel); cursor: pointer; text-align: left;
    box-shadow: 0 1px 2px rgba(120,104,80,.10), 0 8px 20px -12px rgba(120,104,80,.28);
    transition: box-shadow .2s ease, transform .2s ease;
  }
  /* 悬停抬起并加深暖色投影；:active 落回，模拟一次实际按压。 */
  /* z-index: 2 现在纯粹是抬起的观感，不再是任何东西的回护条款：浮层已被 max-width 钉在本列内，
     「浮层向右压到同排右侧那张卡、被 DOM 在后的它盖住」那条路在几何上已经走不通了。
     留着它是因为卡片抬起 2px 时本就该压住下面的邻居，拿掉只会让抬起显得半途而废。 */
  .priority-card:hover { z-index: 2; transform: translateY(-2px); box-shadow: 0 2px 5px rgba(120,104,80,.13), 0 16px 30px -12px rgba(120,104,80,.34); } .priority-card:active { transform: translateY(0); } .priority-head { display: flex; min-width: 0; align-items: center; gap: 9px; }
  /* 圆形头像：与人物页、热门股票行的头像堆叠同一套语言，人物在任何位置都长成同一个形状。 */
  .priority-avatar { flex: 0 0 auto; width: 26px; height: 26px; border-radius: 50%; object-fit: cover; object-position: center top; background: #e7e1d7; filter: saturate(.78) contrast(1.02); }
  /* 姓名与职位竖排在头像右侧，共用一列；姓名用衬线，和看板标题同一支字体。 */
  .priority-id { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  /* 姓名是这张卡的人物页入口（键盘通路），其余位置由卡片自己的点击监听兜底。
     它是 <button>，所以要显式清掉按钮默认的边框/内边距/底色，并把 font 写全——
     按钮不继承字体，不写就退回浏览器默认的那一支。 */
  .priority-who { min-width: 0; max-width: 100%; overflow: hidden; border: 0; padding: 0; color: var(--text); background: transparent; cursor: pointer; text-align: left; font: 600 15px/1.15 var(--display); letter-spacing: -.01em; text-overflow: ellipsis; white-space: nowrap; } .priority-role { overflow: hidden; color: var(--muted); font-size: 10.5px; line-height: 1.25; text-overflow: ellipsis; white-space: nowrap; }
  /* 序号/分组做成描边方牌，不用信号色：橙色的「01 / 白宫」会和买入绿、卖出红抢注意力。 */
  .priority-kicker { flex: 0 0 auto; margin-left: auto; padding: 4px 7px; border: 1px solid var(--line); border-radius: 4px; color: var(--soft); font: 750 9px/1 var(--mono); letter-spacing: .08em; white-space: nowrap; }
  /* 每个方向是一张竖着的圆角卡片，两张左右并排——不是上下两条横带。
     横带铺满卡片整宽时，「买」和「卖」是同一根柱子的上下两段，方向只能靠读色块里的字；
     并排之后两个方向成了横向并列的两个对象，谁重谁轻一眼比得出来，色块也从「行首的标签」
     变成「这一列的表头」。代价是每侧只剩半个卡片的宽度，所以内部一律改成竖排（见 .flow-meta）。 */
  .priority-flows { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin-top: 11px; align-items: stretch; } .priority-flow { min-width: 0; display: flex; flex-direction: column; gap: 6px; padding: 8px 10px 9px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-2); } .priority-flow.buy { border-color: var(--buy-edge); background: var(--buy-wash); } .priority-flow.sell { border-color: var(--sell-edge); background: var(--sell-wash); }
  /* 空方向保留整张卡片与色块，只把配色换成中性：两张卡的骨架因此一样齐，
     不会因为某一侧没数据就塌掉半张卡，同时色块仍在说明「空的是哪个方向」。 */
  .priority-flow.is-empty { border-color: var(--line); background: var(--panel-2); }
  /* 色块与「N 只 · N 笔」同处卡片顶行：色块是这一列的方向标记，计数是它的表头。
     两者共用一行是竖排的前提——色块若独占一行，每张方向卡就要多出 26px 的空白行。 */
  .flow-head { display: flex; align-items: center; gap: 6px; min-width: 0; }
  /* 色块用实心填充而非浅底深字：旧版色块是浅色底 + 同色字，在白卡上几乎看不出是一块颜色，
     方向只能靠读那个字，等于没有编码。实心块是这一屏里对比度最高的方向信号，而它只占 20px。 */
  .flow-tag { flex: 0 0 auto; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 5px; color: #fff; background: var(--soft); font: 800 11px/1 var(--sans); } .priority-flow.buy .flow-tag { background: var(--buy); } .priority-flow.sell .flow-tag { background: var(--sell); }
  /* 只数与笔数是同一个短语（「5 只 · 5 笔」），留在色块右侧那一行里，段间仍由 CSS 补「·」。
     交易日与披露日则是两件独立的事，各占一行（见 .flow-meta）——这一条只管计数。 */
  .flow-counts { min-width: 0; display: flex; flex-wrap: wrap; column-gap: 5px; row-gap: 1px; color: var(--muted); font-size: 10px; line-height: 1.3; } .flow-counts > span { white-space: nowrap; } .flow-counts > span + span::before { content: "·"; margin-right: 5px; color: var(--line-strong); }
  /* 一个方向列出该窗口内的全部标的（最多 3 枚，其余收进 +N），金额不常驻，悬停时代码上浮出浮层。
     标的按金额上限合计降序，所以「第一枚就是最大的一只」这个旧卡片的信息仍然在，只是不再独占一行。
     每枚 chip 自己是一个 <button>，点了进单股页——所以卡片根节点是 <article> 而不是 <button>：
     按钮套按钮是非法嵌套，整卡可点时 chip 就只能当纯展示。金额仍走浮层（鼠标）与
     chip 自己的 aria-label（键盘/读屏），卡片 aria-label 继续口述两个方向的全部内容。 */
  /* 浮层的定位祖先是这一行，不是被悬停的那枚 chip。并排之后每列只剩半个卡片的宽度，
     chip 常常落在列内 150px 之后，而金额区间本身就有一百多像素——锚在 chip 上左对齐，
     浮层必然冲出这一列、压住旁边那张方向卡的计数行（实测 1440 下压掉「2 只 · 2 笔」的「2 只 · 2 」。）
     锚到整行之后，浮层的宽度上限就是列的宽度，溢出这一列在几何上不再可能。
     代价是浮层不再逐枚对准 chip，改为浮在整行 chips 上方、左缘对齐列的左缘。 */
  .flow-chips { position: relative; display: flex; flex-wrap: wrap; gap: 5px; }
  /* chip 自己不能定位：一旦它是 relative，浮层就又挂回 chip，max-width 的上限随之失效。
     cursor 回到 pointer：这枚 chip 现在点了就进单股页，可点比「这里还有内容」更该被预告。
     浮层仍在悬停时出现，两件事互不冲突。 */
  .flow-chip { display: inline-flex; align-items: center; padding: 3px 8px; border: 0; border-radius: 5px; cursor: pointer; font: 700 11.5px/1.25 var(--mono); letter-spacing: .01em; }
  /* 配色沿用 --buy-wash / --sell-wash 与 --buy / --sell，和卡片上方那条方向横带、单股页的胶囊同源：
     同一个方向在同一页出现两种绿，比照搬参考看板的色号更糟。 */
  .flow-chip.buy { color: var(--buy); background: var(--buy-wash); } .flow-chip.sell { color: var(--sell); background: var(--sell-wash); }
  /* +N 是唯一一枚没有去处的 chip（它代表的是被折起来的几只，不是一个代码），
     所以它仍是 <span>、仍是 help：可点的那些已经换成 pointer，这一枚靠光标就分得出来。 */
  .flow-chip.more { color: var(--muted); background: var(--panel-2); cursor: help; box-shadow: inset 0 0 0 1px var(--line); }
  /* left: 0 对齐的是整行 chip 的左缘，max-width: 100% 把上限钉死在列宽上——这两条合起来保证
     浮层永远不越出本列。width: fit-content 让短浮层仍旧贴合内容，只有长浮层才吃满列宽。
     折行后仍可能遇到长到放不下的单段（金额区间中间没有可断点），overflow-wrap: anywhere 兜底，
     宁可把区间折成两行，也不让它带着深色底冲出列外变成一段读不出背景的裸字。 */
  .chip-tip { position: absolute; left: 0; bottom: calc(100% + 6px); z-index: 1; width: fit-content; max-width: 100%; padding: 5px 8px; border-radius: 6px; color: #fff; background: rgba(23,33,43,.96); overflow-wrap: anywhere; opacity: 0; visibility: hidden; transition: opacity .14s ease; pointer-events: none; font: 600 11px/1.5 var(--sans); font-variant-numeric: lining-nums tabular-nums; } .flow-chip:hover .chip-tip { opacity: 1; visibility: visible; }
  /* 交易日与披露日各占一行，不再用「·」串成一句。竖排卡片里横向只剩半个卡片的宽度，
     串成一行时「最新 9月10日买入 · 9月14日披露」会被挤到折行，两段的边界反而消失；
     竖排之后读起来是「一行一件事」，两个日期也更容易两两对照。
     每段仍是 nowrap：这两行是这张卡的载荷，必须整段可读、必须始终分得清，
     放不下时该整段溢出，而不是把「9月14日披露」从中间截断。 */
  .flow-meta { display: flex; flex-direction: column; gap: 1px; color: var(--muted); font-size: 10px; line-height: 1.3; } .flow-meta > span { white-space: nowrap; } .flow-empty { color: var(--muted); font-size: 10px; } .avatar { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; object-position: center top; background: #e5dfd5; } .person-link-button { border: 0; padding: 0; color: inherit; background: transparent; cursor: pointer; font: inherit; text-align: left; } .text-person-link { border: 0; padding: 0; color: inherit; background: transparent; cursor: pointer; font: inherit; text-align: left; transition: color .18s ease; } .text-person-link:hover { color: var(--signal); } .avatar-person-link { display: inline-grid; place-items: center; border: 0; padding: 0; background: transparent; cursor: pointer; } .avatar-person-link:hover img { outline: 2px solid var(--signal); outline-offset: 2px; } .ticker-link-button { border: 0; padding: 0; color: inherit; background: transparent; cursor: pointer; font: inherit; text-align: left; transition: color .18s ease; } .ticker-link-button:hover { color: var(--signal); } .person-link-button:focus-visible, .text-person-link:focus-visible, .avatar-person-link:focus-visible, .ticker-link-button:focus-visible,
  .priority-who:focus-visible, .flow-chip:focus-visible, .consensus-person:focus-visible { outline: 2px solid var(--signal); outline-offset: 3px; }
  /* 「国会山买入共识」：一块一窗口，两块同时呈现，与下方表格区块同构——
     窗口大标题在卡片区之上，标题属于这一块而不属于某张卡。
     卡片照参考看板做成左右双栏：左栏铺买入色浅底放代码、公司、窗口涨跌，右栏白底放买入合计与前三位人物，
     中间一条发丝线分隔。左栏用 --buy-wash 而不是参考图的薄荷色号——这一屏所有买入语义都已经是这个绿。 */
  .consensus-block { margin-top: 26px; } .consensus-block + .consensus-block { margin-top: 34px; } .consensus-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 18px; align-items: stretch; }
  /* overflow: hidden 让左栏的浅底被卡片圆角裁住，否则四角会露出直角的色块。 */
  /* 整张卡进单股页，所以 cursor: pointer 而且悬停要抬起——一张「哪都能点」的卡若没有任何
     悬停反馈，读者只会当它是一块静态版面。抬起沿用人物卡那一套，两处卡片的可点感才是同一个语言。 */
  .consensus-card { display: grid; grid-template-columns: minmax(0, 128px) minmax(0, 1fr); overflow: hidden; border-radius: 10px; background: var(--panel); cursor: pointer; box-shadow: 0 1px 2px rgba(120,104,80,.10), 0 8px 20px -12px rgba(120,104,80,.28); transition: box-shadow .2s ease, transform .2s ease; } .consensus-card:hover { z-index: 2; transform: translateY(-2px); box-shadow: 0 2px 5px rgba(120,104,80,.13), 0 16px 30px -12px rgba(120,104,80,.34); } .consensus-ticker { display: flex; flex-direction: column; gap: 4px; padding: 14px 13px; border-right: 1px solid var(--line); background: var(--buy-wash); } .consensus-ticker > b { color: var(--text); font: 700 22px/1.1 var(--display); letter-spacing: -.01em; } .consensus-ticker > small { color: var(--muted); font-size: 10px; line-height: 1.35; }
  /* 涨跌色由全局 .positive / .negative 给，这里不写 color——写了会以同权重按源序压过那条规则。
     缺行情时 returnClass 返回空串，于是不着色，"—" 既不是涨也不是跌。 */
  .consensus-return { display: flex; flex-direction: column; gap: 2px; margin-top: auto; font: 650 15px/1.1 var(--mono); font-variant-numeric: tabular-nums; } .consensus-return small { color: var(--muted); font-size: 9.5px; font-weight: 500; } .consensus-body { display: flex; flex-direction: column; min-width: 0; padding: 13px 14px 12px; } .consensus-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding-bottom: 9px; border-bottom: 1px solid var(--line); } .consensus-head b { color: var(--text); font: 650 12.5px/1.2 var(--sans); font-variant-numeric: lining-nums tabular-nums; } .consensus-pill { flex: 0 0 auto; padding: 3px 8px; border-radius: 999px; color: var(--buy); background: var(--buy-wash); font: 700 10px/1.3 var(--sans); white-space: nowrap; } .consensus-people { display: flex; flex-direction: column; margin: 0; padding: 0; list-style: none; } .consensus-people li { display: flex; align-items: center; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--line); } .consensus-people li:last-child { border-bottom: 0; } .consensus-people img { flex: 0 0 auto; width: 26px; height: 26px; border-radius: 50%; object-fit: cover; object-position: center top; background: #e7e1d7; transition: box-shadow .16s ease; }
  /* 头像 + 姓名是这张卡的人物页入口，所以它是一条独立的 <button>，而不是 li 本身：
     整张卡进单股页，人物行必须是一个能停下来、能聚焦的内层目标。金额留在按钮外，
     它属于这张卡（这只股票的买入区间），点它该去的是单股页。 */
  .consensus-person { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 8px; border: 0; padding: 0; color: inherit; background: transparent; cursor: pointer; font: inherit; text-align: left; }
  /* 悬停给两处提示：头像描一圈信号色（与头像堆叠里同一套），姓名转信号色。 */
  .consensus-person:hover img { box-shadow: 0 0 0 2px var(--signal); } .consensus-person:hover b { color: var(--signal); } .consensus-person > span { min-width: 0; display: flex; flex-direction: column; gap: 1px; } .consensus-person > span b { overflow: hidden; color: var(--text); font: 600 12px/1.2 var(--sans); text-overflow: ellipsis; white-space: nowrap; } .consensus-person > span small { overflow: hidden; color: var(--muted); font-size: 9.5px; line-height: 1.25; text-overflow: ellipsis; white-space: nowrap; }
  /* 金额贴右缘：三行金额落在同一条竖线上，才能直接比出谁是第一买家。 */
  .consensus-amount { margin-left: auto; color: var(--text); font: 700 11.5px/1.2 var(--mono); white-space: nowrap; font-variant-numeric: tabular-nums; } .consensus-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: auto; padding-top: 9px; border-top: 1px solid var(--line); color: var(--muted); font-size: 9.5px; line-height: 1.4; } .consensus-link { flex: 0 0 auto; padding: 4px 8px; border: 1px solid var(--line-strong); border-radius: 5px; color: var(--soft); background: transparent; cursor: pointer; font: 700 10px/1.3 var(--sans); white-space: nowrap; transition: color .18s ease, border-color .18s ease; } .consensus-link:hover { color: var(--signal); border-color: var(--signal-edge); } .consensus-link:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; } .consensus-empty { padding: 40px 20px; color: var(--muted); text-align: center; font-size: 11px; } .dashboard-explorer { margin-top: 38px; }
  /* 吸附态下点 30/90，窗口标题要停在停靠栏下缘之下，否则会被停靠栏吃掉——
     偏移量跟着 --dock-h 走，吸附前后各算各的（未吸附时停靠栏已经滚出视口，
     留 16px 只是不让标题贴住视口顶边）。 */
  .window-block { scroll-margin-top: calc(var(--dock-h) + 16px); } .window-block + .window-block { margin-top: 34px; }
  /* 30/90 各自是一张独立的表：标题在表格边框外面、作为表格上方的大标题，表格本身才是 .panel。
     读者读到的是「标题 → 表」，两张表因此各自成立、分得出边界。
     标题与 .section-head 一样不缩进，因为标题属于表格这个整体，不属于表格内部的列。
     用中性深色而非信号橙，理由同 .priority-kicker：橙色会和买入绿、卖出红抢注意力。 */
  .window-block-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 14px; margin: 0 0 12px; } .window-block-title { margin: 0; color: var(--text); font: 600 24px/1.1 var(--display); letter-spacing: -.015em; } .window-block-note { color: var(--muted); font: 500 12px/1.5 var(--sans); } .window-block-note:empty { display: none; }
  /* 视图切换条（热门关注股票 / 标的交易明细）每个窗口块各有一条，落在各自大标题下面、表格第一行。
     比原来的页级切换条矮一档：76px / 17px 的体量在同屏出现两次，会把两个 24px 的窗口大标题压掉。
     不铺底色，白底 + 选中项橙色下划线——它下面紧挨着 .hot-stock-head / .timeline-toolbar 两条
     本身就是 #f7f4ed 的横带，切换条再铺一层同色会和它们糊成一整块表头。
     两条 Tab 条联动：点任意一条两块一起切，所以这里只是同一份状态的第二个出口，不是两套控件。 */
  .window-tabs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-bottom: 1px solid var(--line); } .window-tab { position: relative; min-height: 54px; padding: 0 20px; border: 0; border-right: 1px solid var(--line); color: var(--muted); background: transparent; cursor: pointer; text-align: left; font: 650 15px/1 var(--sans); letter-spacing: -.015em; transition: color .18s ease, background .18s ease; } .window-tab:last-child { border-right: 0; }
  /* 下划线压到切换条那根底边框上（bottom:-1px），选中态才是一条完整的粗线，而不是线下面又露一条细线。 */
  .window-tab::after { content: ""; position: absolute; right: 0; bottom: -1px; left: 0; height: 3px; background: transparent; } .window-tab:hover { color: var(--text); background: #faf7f1; } .window-tab.active { color: var(--text); } .window-tab.active::after { background: var(--signal); } .window-view[hidden] { display: none; } .panel { border: 1px solid var(--line); background: var(--panel); } .panel-title { padding: 18px 20px; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 14px; } .panel-title h3 { margin: 0; font: 500 22px/1 var(--display); } .panel-title span { color: var(--muted); font: 650 9px/1 var(--mono); letter-spacing: .08em; } .segmented.compact { padding: 3px; } .segmented.compact button { padding: 7px 10px; font-size: 9px; } .timeline-toolbar { display: grid; grid-template-columns: minmax(190px, 1fr) auto auto auto; gap: 10px; align-items: center; padding: 12px 20px; border-bottom: 1px solid var(--line); background: #f7f4ed; }
  /* 笔数计数跟着工具栏走：两块表各有各的筛选，计数就必须贴着各自那条工具栏，
     不能再像原来那样挂在窗口标题旁边（标题属于整块，计数只属于被筛过的那张表）。 */
  .timeline-count { color: var(--muted); font: 650 10px/1 var(--mono); letter-spacing: .08em; white-space: nowrap; text-align: right; } .timeline-search { position: relative; min-width: 0; } .timeline-search span { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: #667078; font: 700 8px/1 var(--mono); letter-spacing: .08em; pointer-events: none; } .timeline-search input { width: 100%; height: 38px; padding: 0 12px 0 74px; color: var(--text); background: #fff; border: 1px solid var(--line); outline: 0; font-size: 13px; } .timeline-search input:focus { border-color: var(--signal); } .filter-group { display: flex; align-items: center; border: 1px solid var(--line); background: #fff; } .filter-group button { height: 32px; padding: 0 10px; color: var(--muted); background: transparent; border: 0; border-right: 1px solid var(--line); font: 700 9px/1 var(--mono); cursor: pointer; } .filter-group button:last-child { border-right: 0; } .filter-group button.active { color: #fff; background: var(--signal); } .instrument-filter { height: 38px; padding: 0 30px 0 10px; color: var(--soft); background: #fff; border: 1px solid var(--line); font: 700 11px/1 var(--mono); cursor: pointer; } .timeline { padding: 0 20px; } .timeline-row { display: grid; grid-template-columns: 42px minmax(155px,.9fr) minmax(190px,1.25fr) minmax(135px,.8fr) 96px; gap: 14px; align-items: center; min-height: 92px; border-bottom: 1px solid var(--line); cursor: pointer; } .timeline-row:last-child { border: 0; } .timeline-row:hover .security { color: var(--signal); } .timeline-row .avatar { width: 42px; height: 42px; } .who b { display: block; font-size: 13px; margin-bottom: 5px; } .who small, .meta { color: var(--muted); font-size: 10px; } .security { font-weight: 700; font-size: 15px; transition: color .15s; } .security small { display: block; color: var(--muted); font-weight: 400; font-size: 10px; margin-top: 5px; } .trade-type { display: inline-flex; gap: 7px; align-items: center; font: 750 12px/1 var(--mono); text-transform: uppercase; } .trade-type::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; } .trade-type.buy { color: var(--buy); } .trade-type.sell { color: var(--sell); } .trade-type.other { color: var(--muted); } .amount { margin-top: 7px; color: var(--soft); font: 650 11px/1 var(--mono); } .return { text-align: right; font: 800 19px/1 var(--mono); } .return small { display: block; margin-top: 7px; color: var(--muted); font: 400 9px/1.2 var(--sans); } .timeline-empty { padding: 56px 20px; text-align: center; color: var(--muted); font-size: 12px; } .positive { color: var(--buy); } .negative { color: var(--sell); }
  /* 表骨架照参考看板的季度表：表头自带底色 + 一条较重的下边线，行间只用发丝线，整行悬停提亮，
     末行收掉下边线。表头是真的 <thead>，因为排序按钮要住在表头单元格里。 */
  /* min-width: 0 是用来压掉全局 table{min-width:850px} 的：那条规则服务于包在 .stock-table-wrap
     里横向滚动的宽表，热门股票表要跟着面板收缩，不能继承它。固定列宽则是因为「买入 / 卖出」一列
     按内容自动分配时会被两侧挤窄，两条明细里的日期就会被省略号截断。 */
  .hot-stock-table { width: 100%; min-width: 0; table-layout: fixed; border-collapse: collapse; } .hot-stock-table th:nth-child(1) { width: 200px; }
  /* 250px 是「6 位人物 · 6 笔披露 · 买卖分歧」一行 + 六个头像（最宽的一种）量出来的：
     再窄一点，人物数、笔数和方向标签就会各自折成两行。 */
  .hot-stock-table th:nth-child(2) { width: 250px; } .hot-stock-table th:nth-child(4) { width: 165px; } .hot-stock-table thead { background: #f7f4ed; } .hot-stock-table th { padding: 13px 20px; border-bottom: 1px solid var(--line-strong); color: var(--soft); text-align: left; vertical-align: middle; font: 700 11px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; white-space: nowrap; } .hot-stock-table th:last-child { text-align: right; white-space: normal; } .hot-stock-table td { padding: 19px 20px; border-bottom: 1px solid var(--line); vertical-align: middle; } .hot-stock-table tbody tr:last-child td { border-bottom: 0; }
  /* 表格布局里行高是下限不是定值：一行有买卖两条明细、一行只有一条，靠这个下限保持等高。 */
  .hot-stock-table tbody tr { height: 188px; transition: background .18s ease; } .hot-stock-table tbody tr:hover { background: #faf7f0; }
  /* 排序按钮照参考看板：默认一枚浅色 ↕，激活后描蓝边、白底、字变蓝、箭头换成 ↑/↓。
     激活态同时给边框、底色、字色、箭头四个线索，不靠单一线索交代当前排序列与方向。
     负外边距让按钮自己的内边距吃掉表头内边距，按钮文字仍与表头左缘（或右缘）对齐。 */
  .hs-sort { display: inline-flex; align-items: center; gap: 5px; margin: -5px -8px; padding: 5px 8px; border: 1px solid transparent; border-radius: 7px; color: inherit; background: transparent; font: inherit; letter-spacing: inherit; cursor: pointer; white-space: nowrap; transition: color .16s ease, background .16s ease, border-color .16s ease; } .hs-sort::after { content: '↕'; color: var(--line-strong); font-size: 10px; } .hs-sort:hover { color: var(--info); background: #fff; } .hs-sort.active { border-color: var(--info-edge); color: var(--info); background: #fff; box-shadow: 0 1px 3px rgba(38,52,74,.08); } .hs-sort.active.asc::after { content: '↑'; color: var(--info); } .hs-sort.active.desc::after { content: '↓'; color: var(--info); } .hs-sort:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; } .hot-stock-return-head small { display: block; margin-top: 6px; color: var(--muted); font: 500 9px/1.35 var(--sans); letter-spacing: 0; text-transform: none; } .hot-stock-scope { padding: 12px 20px; border-bottom: 1px solid var(--line); color: var(--muted); background: #fffaf0; font-size: 12px; line-height: 1.6; } .hot-stock-scope b { margin-right: 8px; color: var(--signal); font: 750 10px/1 var(--mono); letter-spacing: .06em; } .hot-stock-identity, .hot-stock-return { border: 0; padding: 0; color: inherit; background: transparent; cursor: pointer; text-align: left; } .hot-stock-identity { display: grid; grid-template-columns: 27px minmax(0,1fr); gap: 11px; align-items: start; transition: color .18s ease, transform .18s ease; } .hot-stock-identity:hover { color: var(--signal); transform: translateX(2px); } .hot-stock-rank { padding-top: 4px; color: #667078; font: 750 8px/1 var(--mono); } .hot-stock-ticker { display: block; font: 800 23px/.95 var(--mono); letter-spacing: -.03em; } .hot-stock-company { display: block; max-width: 160px; margin-top: 8px; overflow: hidden; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; } .hot-stock-people { min-width: 0; } .hot-stock-attention { display: flex; align-items: center; gap: 8px; } .hot-stock-attention b { font: 750 14px/1 var(--mono); } .hot-stock-attention span { color: var(--muted); font-size: 9px; } .hot-stock-signal { padding: 5px 6px; color: var(--muted); border: 1px solid var(--line); font: 700 7px/1 var(--mono); } .hot-stock-signal.buy { color: var(--buy); border-color: var(--buy-edge); } .hot-stock-signal.sell { color: var(--sell); border-color: var(--sell-edge); } .hot-stock-signal.mixed { color: var(--signal); border-color: var(--signal-edge); } .hot-stock-avatar-stack { display: flex; margin: 12px 0 0 6px; } .hot-stock-avatar-stack .avatar-person-link { width: 34px; height: 34px; margin-left: -6px; border: 2px solid var(--panel); border-radius: 50%; overflow: hidden; transition: transform .16s ease, box-shadow .16s ease; }
  /* 这里的悬停反馈必须画在按钮自己身上，不能沿用全局那条 .avatar-person-link:hover img 的 outline：
     按钮要 overflow: hidden 才能把方形肖像裁成圆（见上一条），而溢出裁剪正好切在 img 的边框盒上，
     那条 outline-offset: 2px 因此整圈被裁掉——实测悬停毫无变化。box-shadow 画在元素自己的边框盒之外，
     不受自身 overflow 影响，于是描边出得来。
     z-index 负责盖住右邻：堆叠靠 -6px 的负外边距重叠，不抬起来的话描边会被 DOM 在后的头像压掉一半。
     按钮是 flex 子项，z-index 对静态定位的 flex 子项同样生效，不必再加 position。 */
  .hot-stock-avatar-stack .avatar-person-link:hover,
  .hot-stock-avatar-stack .avatar-person-link:focus-visible { z-index: 2; transform: translateY(-3px) scale(1.14); box-shadow: 0 0 0 2px var(--signal), 0 7px 12px -5px rgba(60,48,32,.5); } .hot-stock-avatar-stack img { width: 100%; height: 100%; object-fit: cover; object-position: center top; } .hot-stock-more { width: 34px; height: 34px; display: grid; place-items: center; margin-left: -6px; border: 2px solid var(--panel); border-radius: 50%; color: var(--muted); background: #e5dfd5; font: 750 10px/1 var(--mono); } .hot-stock-flow { min-width: 0; display: flex; flex-direction: column; gap: 8px; } .hot-stock-flow-rows { display: flex; flex-direction: column; gap: 7px; } .hot-stock-flow-row { display: grid; grid-template-columns: 30px minmax(60px, auto) minmax(0, auto) minmax(0, 1fr); align-items: baseline; column-gap: 10px; row-gap: 2px; padding-left: 8px; border-left: 2px solid var(--line-strong); } .hot-stock-flow-row.buy { border-left-color: var(--buy); } .hot-stock-flow-row.sell { border-left-color: var(--sell); } .hot-stock-flow-row.is-empty { border-left-color: var(--line); } .hs-side { color: var(--muted); font: 700 10px/1.4 var(--sans); } .hs-figures { color: var(--soft); font: 650 10px/1.4 var(--mono); white-space: nowrap; } .hs-amount { min-width: 0; overflow: hidden; color: var(--text); font: 700 12px/1.3 var(--mono); text-align: left; text-overflow: ellipsis; white-space: nowrap; } .hs-dates { grid-column: 4; min-width: 0; overflow: hidden; color: var(--muted); font: 600 10px/1.3 var(--mono); text-align: right; text-overflow: ellipsis; white-space: nowrap; } .hs-empty { grid-column: 2 / -1; color: var(--muted); font: 600 10px/1.4 var(--mono); } .hot-stock-flow .split-bar { height: 11px; } .hot-stock-amount { min-width: 0; overflow: hidden; color: var(--muted); font: 650 9px/1 var(--mono); text-align: right; text-overflow: ellipsis; white-space: nowrap; } .hot-stock-return { color: var(--text); text-align: right; font: 800 19px/1 var(--mono); transition: color .18s ease, transform .18s ease; }
  /* .positive/.negative 与 .hot-stock-return 同为单类选择器(0,1,0)，同分时靠源码顺序决胜，
     而 .hot-stock-return 声明了 color: var(--text)，写在后面就把涨跌色压回深灰——+9.1% 曾因此不着色。
     这里用复合选择器(0,2,0)把涨跌色提回来，不必依赖规则块的位置。:hover 同为(0,2,0)且排在后面，悬停仍变信号色。 */
  .hot-stock-return.positive { color: var(--buy); } .hot-stock-return.negative { color: var(--sell); } .hot-stock-return:hover { color: var(--signal); transform: translateX(-2px); } .hot-stock-return small { display: block; margin-top: 7px; color: var(--muted); font: 500 9px/1.45 var(--sans); white-space: nowrap; } .hot-stock-identity:focus-visible, .hot-stock-return:focus-visible, .hot-stock-row .bar-segment:focus-visible { outline: 2px solid var(--signal); outline-offset: 3px; }
  /* 空态是 <td colspan>，要盖过 .hot-stock-table td 的内边距，所以连表格类名一起写。 */
  .hot-stock-table td.hot-stock-empty { padding: 52px 20px; color: var(--muted); text-align: center; font-size: 11px; }
  /* 红绿条按参考图改成胶囊：整条是圆角轨道，绿段和红段各自两端全圆角，中间留一道白间隙。
     两色直接相接时，交界处会被读成一个渐变，留白才让「两块」读得出来。轨道因此改用白底 +
     内描边，而不是原来的灰底——间隙要露出的是白色（参考图里那道缝比周围背景亮）；有记录时
     轨道被各段盖满看不出底，只有一条记录都没有时才会剩下一枚白胶囊加细边。
     段高 = height 12 - 上下 padding 各 2 = 8px。不设 overflow: hidden：段本身已经全圆角、
     又内缩 2px，不需要裁剪，而 hidden 会把 hover 的 scaleY(1.25)（8 → 10px）和
     .bar-segment:focus-visible 那圈 outline-offset: 3px 一起裁掉——焦点环本来就该画在
     轨道外面，裁掉等于键盘用户看不到焦点。 */
  .split-bar { display: flex; width: 100%; height: 12px; padding: 2px; gap: 3px; border-radius: 999px; background: #fff; box-shadow: inset 0 0 0 1px var(--line); } .bar-segment { min-width: 4px; height: 100%; padding: 0; border: 0; border-radius: 999px; cursor: pointer; transition: filter .18s ease, transform .18s ease; } .bar-segment:hover { filter: brightness(1.22); transform: scaleY(1.25); } .bar-segment.buy { background: var(--buy); } .bar-segment.sell { background: var(--sell); } .bar-segment.other { background: #606a71; } .balance-labels { display: flex; justify-content: space-between; gap: 8px; margin-top: 8px; font: 750 10px/1 var(--mono); } .balance-labels .buy { color: var(--buy); } .balance-labels .sell { color: var(--sell); } .secondary { margin-top: 18px; } table { width: 100%; border-collapse: collapse; min-width: 850px; } th { color: var(--muted); font: 650 9px/1 var(--mono); text-align: left; letter-spacing: .08em; padding: 12px 16px; border-bottom: 1px solid var(--line); } td { padding: 15px 16px; border-bottom: 1px solid var(--line); color: var(--soft); font-size: 11px; } td strong { color: var(--text); } .source-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; background: var(--line); } .source-link { min-width: 0; min-height: 96px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 20px; color: var(--text); background: var(--panel); text-decoration: none; transition: background .18s ease, color .18s ease; } .source-link:hover { color: var(--signal); background: var(--panel-2); } .source-link:focus-visible { outline: 2px solid var(--signal); outline-offset: -3px; } .source-link-copy { min-width: 0; } .source-link-copy b { display: block; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; } .source-link-copy small { display: block; margin-top: 8px; color: var(--muted); font: 650 9px/1.35 var(--mono); } .source-link-action { flex: 0 0 auto; color: var(--muted); font: 750 9px/1 var(--mono); } .source-empty { grid-column: 1 / -1; padding: 28px 20px; color: var(--muted); background: var(--panel); font-size: 11px; text-align: center; } .source-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 7px; background: var(--buy); vertical-align: 1px; } .source-dot.delayed { background: var(--signal); } .footnote { color: #697278; font-size: 10px; line-height: 1.7; padding-top: 28px; } .drawer-backdrop { position: fixed; inset: 0; background: rgba(67,60,49,.38); z-index: 80; opacity: 0; pointer-events: none; transition: opacity .2s; } .drawer { position: fixed; z-index: 90; right: 0; top: 0; width: min(540px, 100%); height: 100%; background: #fff; border-left: 1px solid var(--line-strong); transform: translateX(102%); transition: transform .26s ease; overflow-y: auto; } .drawer.open { transform: translateX(0); } .drawer-backdrop.open { opacity: 1; pointer-events: auto; } .drawer-close { position: absolute; right: 18px; top: 18px; z-index: 2; border: 1px solid rgba(23,33,43,.18); background: rgba(255,255,255,.88); color: var(--text); width: 40px; height: 40px; cursor: pointer; } .drawer-hero { height: 260px; position: relative; overflow: hidden; } .drawer-hero img { width: 100%; height: 100%; object-fit: cover; object-position: center 18%; filter: saturate(.65); } .drawer-hero::after { content: ""; position: absolute; inset: 0; background: linear-gradient(0deg,#fff,transparent 65%); pointer-events: none; } .drawer-person-photo { width: 100%; height: 100%; display: block; } .drawer-body { padding: 0 28px 38px; } .drawer-body h2 { margin: -35px 0 7px; position: relative; z-index: 1; font: 500 42px/.96 var(--display); } .drawer-person-name { color: var(--text); font: inherit; } .drawer-person-name:hover { color: var(--signal); } .drawer-role { color: var(--muted); font-size: 11px; } .drawer-callout { margin-top: 25px; padding: 18px; border-left: 3px solid var(--signal); background: #faf7f0; } .drawer-callout b { font: 700 10px/1 var(--mono); color: var(--signal); letter-spacing: .08em; } .drawer-callout h3 { margin: 10px 0 6px; font-size: 21px; } .drawer-callout p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; } .fact-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); margin-top: 20px; } .fact { background: #fff; padding: 14px; min-height: 68px; } .fact span { display: block; color: var(--muted); font-size: 9px; margin-bottom: 7px; } .fact b { font: 650 12px/1.35 var(--mono); } .direction-summary-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 11px; } .direction-summary-head b { color: var(--soft); font-size: 11px; } .direction-summary-head span { color: var(--muted); font: 700 9px/1 var(--mono); } .stock-page { display: none; position: fixed; inset: 0; z-index: 70; overflow-y: auto; background: var(--bg); color: var(--text); } .stock-page.open { display: block; } body.stock-view-open { overflow: hidden; } .stock-shell { width: min(1420px, calc(100% - 48px)); margin: 0 auto; padding-bottom: 70px; } .stock-nav { position: sticky; top: 0; z-index: 3; min-height: 72px; display: flex; align-items: center; justify-content: space-between; gap: 18px; background: rgba(244,241,232,.96); border-bottom: 1px solid var(--line); } .stock-back { border: 0; padding: 9px 0; color: var(--soft); background: transparent; cursor: pointer; font: 700 10px/1 var(--mono); letter-spacing: .04em; } .stock-back:hover { color: var(--signal); } .stock-nav-actions { display: flex; align-items: center; gap: 13px; } .stock-review-label { color: var(--signal); font: 700 9px/1 var(--mono); letter-spacing: .08em; }
  /* 首屏要露出「政客交易明细」的表头，整块顶区按实测逐项压低。最大的两项是：
     ① 右栏 .stock-summary 是 5–6 行正文，它决定 .stock-heading 这一行的高度（左栏的
     代码块比它矮），所以摘要的字号和行高是这里最有效的杠杆；
     ② .stock-glyph 与 .stock-ticker 的高度差——两者取大值，68px 的方块会把代码行的
     高度顶到 75px 以上，方块收到 50px、代码上限收到 54px 之后这一行才真正变矮。
     其余为逐条微调，每处 3–12px。 */
  .stock-hero { padding: 22px 0 14px; } .stock-heading { display: grid; grid-template-columns: minmax(0,1fr) minmax(340px,.8fr); gap: 40px; align-items: end; } .stock-id { display: flex; align-items: center; gap: 15px; } .stock-glyph { width: 50px; height: 50px; display: grid; place-items: center; color: #fff; background: var(--signal); font: 800 17px/1 var(--mono); } .stock-ticker { margin: 0; font: 650 clamp(34px,4.2vw,54px)/.92 var(--mono); letter-spacing: -.06em; } .stock-company { margin-top: 8px; color: var(--muted); font-size: 13px; } .stock-cutoff { margin-top: 8px; display: inline-flex; padding: 5px 8px; border: 1px solid var(--line); color: var(--muted); font: 700 9px/1 var(--mono); } .stock-summary { padding: 12px 15px; border-left: 3px solid var(--signal); background: #fff; color: var(--soft); font-size: 12.5px; line-height: 1.55; } .stock-summary b { color: var(--signal); } .stock-status { display: flex; gap: 8px; margin-top: 10px; } .stock-status span { padding: 5px 8px; border: 1px solid var(--line); color: var(--muted); font: 700 9px/1 var(--mono); }
  /* 三格：关联政客 / 官员（头像）、现价、披露金额区间。原来那格「政客交易 N 笔」已删，
     与下方「政客交易明细」卡头的笔数重复。现价格按「涨跌数字是主角」重排：
     价格 15px 单色、涨跌百分比 32px 行宽体——两者字号差要一眼可辨。
     首屏压缩把两者一起向下调（15→13、32→26），倍数保持 2.0× 不变：
     「涨跌数字放大、价格相对变小」说的是两者的相对关系，同步缩小不改变这个关系，
     只缩价格会反过来把价格变成重点。价格不再往下压是因为它下面是 11px 的标签行，
     再小就分不出「标签」和「值」。 */
  .stock-metrics { display: grid; grid-template-columns: repeat(3,1fr); margin-top: 14px; border: 1px solid var(--line); } .stock-metric { min-width: 0; padding: 10px 14px; border-right: 1px solid var(--line); } .stock-metric:last-child { border-right: 0; } .stock-metric span { display: block; color: var(--muted); font-size: 9px; margin-bottom: 6px; } .stock-metric b { display: block; font: 600 18px/1 var(--display); white-space: nowrap; } .stock-metric small { display: block; margin-top: 5px; color: var(--muted); font: 500 9px/1.3 var(--sans); } .stock-metric small strong { font: 800 13px/1 var(--mono); }
  /* 复合选择器 (0,2,1) 压过 .stock-metric b 的基础块与非 media 覆盖块，也压过
     720px 断点里那条字号回落的 .stock-metric b——不靠源码顺序决胜。 */
  .stock-metric b.stock-metric-price { margin-top: 2px; color: var(--soft); font: 600 13px/1 var(--mono); } .stock-metric b.stock-metric-change { margin-top: 5px; font: 800 26px/1 var(--mono); }
  /* 与 .hot-stock-return.positive 同一处理：涨跌色写成复合选择器 (0,2,0)，
     不靠 .stock-metric-change 与 .positive 的源码先后决胜。 */
  .stock-metric-change.positive { color: var(--buy); } .stock-metric-change.negative { color: var(--sell); }
  /* 头像要点得动，所以照搬热门股票那套堆叠：圆形靠 overflow: hidden 裁出，
     悬停反馈必须画在按钮自己的 box-shadow 上——img 上的 outline 会被这层裁剪切掉。
     这里不套用全局 .avatar-person-link:hover img 的 outline。 */
  .stock-metric-people { display: flex; align-items: center; margin-top: 4px; } .stock-metric-people .avatar-person-link { position: relative; width: 30px; height: 30px; margin-left: -8px; border: 2px solid var(--panel); border-radius: 50%; overflow: hidden; transition: transform .16s ease, box-shadow .16s ease; } .stock-metric-people .avatar-person-link:first-child { margin-left: 0; } .stock-metric-people .avatar-person-link img { display: block; width: 100%; height: 100%; object-fit: cover; } .stock-metric-people .avatar-person-link:hover,
  .stock-metric-people .avatar-person-link:focus-visible { z-index: 2; transform: translateY(-3px) scale(1.14); box-shadow: 0 0 0 2px var(--signal), 0 7px 12px -5px rgba(60,48,32,.5); } .stock-metric-more { margin-left: 7px; color: var(--muted); font: 700 11px/1 var(--mono); cursor: help; }
  /* 买卖结构条夹在卡头与明细表之间，是首屏里表格之前的最后一段非表格内容，一并压薄。 */
  .stock-direction { margin: 10px 0 0; padding: 11px; border: 1px solid var(--line); background: #fff; } .stock-direction .direction-summary-head { margin-bottom: 8px; } .stock-direction .split-bar { height: 12px; } .stock-direction .balance-labels { margin-top: 7px; font-size: 10px; } .stock-quarter { margin-top: 10px; border: 1px solid var(--line); background: #fff; } .stock-quarter-head { padding: 9px 16px 3px; color: var(--muted); font-size: 10px; } .stock-quarter-grid { display: grid; grid-template-columns: repeat(3,1fr); } .stock-quarter-item { min-width: 0; padding: 6px 16px 10px; } .stock-quarter-item span { display: block; color: var(--muted); font-size: 9px; } .stock-quarter-item b { display: block; margin-top: 5px; font: 600 18px/1 var(--display); } .stock-quarter-note { margin: 0 16px; padding: 7px 0 9px; border-top: 1px solid var(--line); color: var(--muted); font-size: 9px; line-height: 1.5; } .stock-signal { margin-top: 10px; padding: 8px 16px; border: 1px solid #d7bd83; background: #fff7df; color: #80530d; font: 600 12.5px/1.45 var(--sans); } .stock-data-section { margin-top: 10px; border: 1px solid var(--line); background: var(--panel); } .stock-politician-intro { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 10px 16px; border-bottom: 1px solid var(--line); } .stock-politician-intro b { font: 500 19px/1.1 var(--display); } .stock-politician-intro p { margin: 7px 0 0; color: var(--muted); font-size: 10px; } .stock-politician-intro .stock-filter { flex: 0 0 auto; }
  /* 单股页「政客交易明细」照参考看板重排：纯文字表头、发丝分隔线、约 62px 行高、圆形头像、
     方向做成胶囊。整块挂在 .stock-disclosure-table 下——.trade-type 在人物页那张 8 列宽表里
     也用到，那里仍是紧凑的点状标记，不能跟着一起变胶囊。
     胶囊配色直接用 --buy-wash / --sell-wash 与 --buy / --sell，和表格上方那条买卖结构条同源：
     同一个方向在同一页出现两种绿，比照搬参考看板的色号更糟。 */
  .stock-disclosure-table { min-width: 760px; }
  /* 表头用正文色正文字体，而不是全局 th 那套 mono 大写小字：这一行的表头就是几个词，
     读者扫的是「金额」「披露日」本身，字距一拉开反而要多认一次。 */
  .stock-disclosure-table th { padding: 15px 20px; border-bottom: 1px solid var(--line-strong); color: var(--text); font: 600 13px/1 var(--sans); letter-spacing: 0; }
  /* 金额与两个日期右对齐：同列数字按位对齐才比得出大小，左对齐时千分位与天数长短互相错位。
     表头一起右对齐，否则表头会浮在数字左上方，读者要多找一次列。 */
  .stock-disclosure-table th:nth-child(n+3), .stock-disclosure-table td:nth-child(n+3) { text-align: right; } .stock-disclosure-table td { padding: 14px 20px; border-bottom: 1px solid var(--line); vertical-align: middle; } .stock-disclosure-table tbody tr:last-child td { border-bottom: 0; }
  /* 表格布局里行高是下限不是定值：披露日那格带一行「延迟 N 天」小字、交易日那格没有，
     靠这个下限把两种行的实际高度补齐，行与行才不会一高一矮。 */
  .stock-disclosure-table tbody tr { height: 64px; } .stock-disclosure-table .stock-person { min-width: 180px; gap: 13px; } .stock-disclosure-table .trade-type { min-width: 42px; justify-content: center; padding: 7px 15px; border-radius: 999px; font: 600 12px/1.2 var(--sans); letter-spacing: 0; text-transform: none; }
  /* 胶囊的底色已经交代了方向，再点一颗同色圆点就是同一件事说两遍。 */
  .stock-disclosure-table .trade-type::before { display: none; } .stock-disclosure-table .trade-type.buy, .stock-disclosure-table .trade-type.purchase { color: var(--buy); background: var(--buy-wash); } .stock-disclosure-table .trade-type.sell, .stock-disclosure-table .trade-type.sale { color: var(--sell); background: var(--sell-wash); } .stock-disclosure-table .trade-type.other { color: var(--muted); background: rgba(101,113,122,.12); }
  /* 参考看板的方向列只有一枚胶囊，本表在胶囊下还有一行「持仓作用 · 交易类型」——
     那是官方证据字段（分类缺失时要照实写「未分类」），不能为了对齐参考把它删掉，改成小字副行。 */
  .stock-disclosure-table .sd-direction { display: grid; justify-items: start; gap: 6px; } .stock-disclosure-table .sd-pills { display: inline-flex; gap: 6px; } .stock-disclosure-table .sd-direction small { color: var(--muted); font-size: 10px; line-height: 1.4; }
  /* 金额和日期从 td 的 --soft 提到正文色：这两列是读者真正要比对的数据，姓名那列已经有自己的色。 */
  .stock-disclosure-table .sd-amount, .stock-disclosure-table .sd-date { color: var(--text); white-space: nowrap; font-variant-numeric: tabular-nums; } .stock-disclosure-table .sd-amount { font-weight: 650; } .stock-disclosure-table .sd-amount small, .stock-disclosure-table .sd-date small { color: var(--muted); font-size: 10px; font-weight: 500; } .stock-price-section { margin-top: 18px; } .stock-price-head { display: flex; align-items: end; justify-content: space-between; gap: 20px; padding: 0 0 14px; } .stock-price-head h2 { margin: 0; font: 500 28px/1 var(--display); } .stock-price-head span { color: var(--muted); font-size: 10px; } .stock-price-frame { padding: 18px 20px 12px; border: 1px solid var(--line); background: var(--panel); overflow-x: auto; } .stock-price-chart { display: block; width: 100%; height: auto; min-height: 300px; } .stock-price-chart .grid-line { stroke: #d8d3c8; stroke-width: 1; stroke-dasharray: 4 5; } .stock-price-chart .axis-line { stroke: #8a928f; stroke-width: 1; } .stock-price-chart .axis-label { fill: #65717a; font: 12px var(--sans); } .stock-price-chart .price-area { fill: rgba(255,202,58,.10); } .stock-price-chart .price-line { fill: none; stroke: var(--signal); stroke-width: 2.4; vector-effect: non-scaling-stroke; }
  /* 游标：跟着鼠标走的绿点 + 竖直虚线，图上**唯一**的绿色指示。它取代了原来的静态上季末
     基准线（评审意见：两条一模一样的绿虚线并排，读不出哪条是活的）。基准价本身没有丢——
     图下说明写着日期和收盘价、现价格也写着「以上季末为基准」，两处都是文字，所以对读屏
     用户没有变得更难拿到；删掉的只是曲线上的那枚静态标记。
     绿点必须落在曲线上、且与悬浮卡报出的收盘价是同一个点，否则同一屏里点、线、数字对不上。
     用 visibility 而不是 display：切 display 会让 SVG 重新布局，快速划过时点会抖。
     pointer-events: none 是必需的——游标若吃掉指针，就会把命中判给自己、把下面的
     .price-hit 挡掉，悬停直接失效。 */
  .stock-price-chart .price-cursor { visibility: hidden; pointer-events: none; } .stock-price-chart .price-cursor.is-on { visibility: visible; } .stock-price-chart .price-cursor-line { stroke: var(--buy); stroke-width: 1.3; stroke-dasharray: 5 5; } .stock-price-chart .price-cursor-dot { fill: var(--buy); stroke: #fff; stroke-width: 2; } .stock-price-chart .latest-dot { fill: var(--signal); stroke: #fff; stroke-width: 2; }
  /* 曲线上的买卖人头像：买在上、卖在下，各自用本方向的色描边，一条虚线引到曲线上的那个点。
     stem 要 pointer-events: none：它是一条又细又长的命中区，留着的话鼠标停在线上会一直命中
     标记，够不到它旁边那段曲线，而「非买卖点也能看股价」正是要够到那里。 */
  .stock-price-chart .price-hit { fill: transparent; } .stock-price-chart .price-mark { cursor: pointer; } .stock-price-chart .price-mark-stem { stroke-width: 1.2; stroke-dasharray: 2 3; pointer-events: none; } .stock-price-chart .price-mark-ring { fill: var(--panel); stroke-width: 2; } .stock-price-chart .price-mark-img { pointer-events: none; }
  /* 引线自己带方向类，不再挂在 .price-mark 里面：所有引线先于所有头像绘制，才不会被后画的
     头像顺着自己的引线一路压过去。选择器因此是 .price-mark-stem.buy 而不是 .price-mark.buy
     下的后代选择器——引线已经不在那个 <g> 里了。 */
  .stock-price-chart .price-mark-stem.buy { stroke: var(--buy); } .stock-price-chart .price-mark.buy .price-mark-ring { stroke: var(--buy); } .stock-price-chart .price-mark-stem.sell { stroke: var(--sell); } .stock-price-chart .price-mark.sell .price-mark-ring { stroke: var(--sell); } .stock-price-chart .price-mark:hover .price-mark-ring { stroke-width: 4; } .stock-price-chart .price-mark-more-ring { fill: var(--panel-2); stroke: var(--muted); stroke-width: 1.5; } .stock-price-chart .price-mark-more-text { fill: var(--muted); pointer-events: none; font: 700 10px var(--mono); } .stock-price-chart .price-mark:hover .price-mark-more-ring { stroke-width: 3; }
  /* z-index 76：压过单股页(70)与人物页(75)，但仍低于抽屉(80/90)，抽屉打开时浮层不会浮在上面。
     fixed 定位按 clientX/clientY 走，单股页内部滚动不影响它，也不用给 .stock-price-frame 加
     position: relative——那个框是 overflow-x: auto，浮层放进去会撑出纵向滚动条。 */
  .price-tip { position: fixed; z-index: 76; max-width: 268px; padding: 9px 11px; border-radius: 8px; background: rgba(23,33,43,.96); color: #fff; opacity: 0; transition: opacity .12s ease; pointer-events: none; font: 600 11.5px/1.55 var(--sans); font-variant-numeric: lining-nums tabular-nums; } .price-tip.is-on { opacity: 1; } .price-tip .tip-buy { color: #7fd6a4; } .price-tip .tip-sell { color: #ff9a95; } .price-tip small { display: block; margin-top: 5px; color: rgba(255,255,255,.62); font: 500 10.5px/1.5 var(--sans); } .stock-price-notes { display: grid; gap: 8px; margin-top: 14px; color: var(--muted); font-size: 10px; line-height: 1.55; } .stock-price-notes b { color: var(--text); font: 700 11px/1 var(--mono); } .stock-price-empty { min-height: 300px; display: grid; place-items: center; color: var(--muted); font-size: 11px; } .stock-page-note { margin-top: 18px; padding: 17px 18px; border: 1px solid var(--line); color: var(--muted); font-size: 10px; line-height: 1.7; } .stock-filter { display: flex; border: 1px solid var(--line); } .stock-filter button { padding: 9px 12px; border: 0; border-right: 1px solid var(--line); color: var(--muted); background: #fff; font: 700 11px/1 var(--mono); cursor: pointer; } .stock-filter button:last-child { border: 0; } .stock-filter button.active { color: #fff; background: var(--signal); } .stock-table-wrap { overflow-x: auto; } .stock-table { min-width: 930px; } .stock-table tbody tr { cursor: pointer; transition: background .15s ease; } .stock-table tbody tr:hover { background: #faf7f0; } .stock-table .stock-person { display: flex; align-items: center; gap: 10px; border: 0; padding: 0; color: inherit; background: transparent; cursor: pointer; text-align: left; }
  /* 圆形头像是参考看板的形状。这条规则和文件上方 .stock-disclosure-table 里那条同权重，
     写在后面所以以这条为准——该头像只出现在这张表里（.stock-person 的唯一样式归属）。 */
  .stock-table .stock-person img { width: 36px; height: 36px; border-radius: 50%; object-fit: cover; object-position: center top; } .stock-table .stock-person b { display: block; color: var(--text); font-size: 11px; } .stock-table .stock-person small { color: var(--muted); font-size: 9px; } .stock-empty { padding: 44px 20px; color: var(--muted); text-align: center; font-size: 11px; } .person-page { display: none; position: fixed; inset: 0; z-index: 75; overflow-y: auto; background: var(--bg); color: var(--text); } .person-page.open { display: block; } body.person-view-open { overflow: hidden; } .person-shell { width: min(1320px, calc(100% - 48px)); margin: 0 auto; padding-bottom: 70px; } .person-nav { position: sticky; top: 0; z-index: 3; min-height: 72px; display: flex; align-items: center; justify-content: space-between; gap: 18px; background: rgba(244,241,232,.96); border-bottom: 1px solid var(--line); } .person-back { border: 0; padding: 9px 0; color: var(--soft); background: transparent; cursor: pointer; font: 700 10px/1 var(--mono); letter-spacing: .04em; } .person-back:hover { color: var(--signal); } .person-nav-actions { display: flex; align-items: center; gap: 13px; } .person-review-label { color: var(--signal); font: 700 9px/1 var(--mono); letter-spacing: .08em; }
  /* 首屏要露出交易明细表的表头，所以标题区整体压低：.person-portrait 的 min-height 是
     这一块的实际高度来源（右侧 .person-identity 内容比它矮），只缩名字和摘要不动它，
     首屏高度一点都不会变——440 → 300 才是真正起作用的那个数。
     名字 96px → 56px 上限、摘要 15px → 13px，是为了让右侧内容在 300px 里仍然排得下。 */
  .person-hero { padding: 30px 0 0; } .person-hero-grid { display: grid; grid-template-columns: minmax(240px,.62fr) minmax(0,1.38fr); gap: 40px; align-items: stretch; } .person-portrait { min-height: 300px; position: relative; overflow: hidden; background: #e7e1d7; } .person-portrait img { width: 100%; height: 100%; position: absolute; inset: 0; object-fit: cover; object-position: center 15%; filter: saturate(.72) contrast(1.03); } .person-portrait::after { content: ""; position: absolute; inset: 0; background: linear-gradient(0deg,rgba(255,255,255,.46),transparent 42%); pointer-events: none; } .person-photo-label { position: absolute; left: 16px; bottom: 16px; z-index: 1; padding: 8px 10px; border: 1px solid rgba(23,33,43,.15); background: rgba(255,255,255,.88); color: var(--soft); font: 700 10px/1 var(--mono); letter-spacing: .05em; } .person-identity { display: flex; min-width: 0; flex-direction: column; justify-content: flex-end; padding: 14px 0 2px; } .person-eyebrow { color: var(--signal); font: 700 10px/1 var(--mono); letter-spacing: .08em; } .person-name { max-width: 820px; margin: 12px 0 10px; font: 500 clamp(34px,4.2vw,56px)/.94 var(--display); letter-spacing: -.05em; text-wrap: balance; } .person-roleline { display: flex; flex-wrap: wrap; gap: 7px; } .person-roleline span { padding: 5px 8px; border: 1px solid var(--line); color: var(--muted); font: 700 9px/1 var(--mono); } .person-summary { max-width: 780px; margin-top: 14px; padding: 13px 16px; border-left: 3px solid var(--signal); background: #fff; color: var(--soft); font-size: 13px; line-height: 1.62; } .person-summary b { color: var(--signal); } .person-source { display: flex; flex-wrap: wrap; gap: 7px 14px; margin-top: 10px; color: var(--muted); font-size: 9px; line-height: 1.5; } .person-source a { color: var(--soft); text-underline-offset: 3px; }
  /* 右栏由 minmax(310px,.55fr) 改成固定 250px：9 列表格不折行需要约 952px（逐行实测各列
     max(表头,内容) + 内边距之和；详见下面 @media 1011/939 两档的注释），旧比例在最宽处
     也只给主栏 944px，1050–1240 一段更是只剩 673–940px。250px 是右栏内容（本人/配偶/
     共同账户、股票/期权/ETF 这类短标签，加上卡片自身 36px 内边距）排得下的宽度，再窄标签
     就要折行；固定而不是按比例，是因为这里的约束是下限——主栏够宽之后，多出来的宽度
     给表格比给右栏有用。 */
  .person-layout { display: grid; grid-template-columns: minmax(0,1fr) 250px; gap: 18px; margin-top: 18px; align-items: start; } .person-main, .person-aside { min-width: 0; display: grid; gap: 18px; } .person-card { min-width: 0; border: 1px solid var(--line); background: var(--panel); } .person-card-head { display: flex; justify-content: space-between; align-items: end; gap: 18px; padding: 20px; border-bottom: 1px solid var(--line); } .person-card-head h3 { margin: 0; font: 500 25px/1 var(--display); } .person-card-head p { margin: 0; color: var(--muted); font-size: 9px; } .person-breakdown { padding: 12px 18px; } .person-breakdown-row { padding: 12px 0; border-bottom: 1px solid var(--line); } .person-breakdown-row:last-child { border: 0; } .person-breakdown-copy { display: flex; justify-content: space-between; gap: 16px; color: var(--muted); font-size: 10px; } .person-breakdown-copy b { color: var(--text); font: 700 10px/1 var(--mono); } .person-breakdown-track { height: 4px; margin-top: 9px; background: #e5dfd5; } .person-breakdown-track i { display: block; height: 100%; background: var(--signal); } .person-note { padding: 18px 20px; color: var(--muted); font-size: 10px; line-height: 1.72; } .person-note b { display: block; margin-bottom: 8px; color: var(--signal); font: 700 9px/1 var(--mono); letter-spacing: .06em; } .person-table-wrap { overflow-x: auto; }
  /* 第 6 列「交易日至今涨跌幅」插在交易日与申报日之间，表格变成 9 列。
     下限不再随列数等比抬高，取与 .stock-disclosure-table 同值的 760px。原来的 930px→1030px 是按列数
     拍的，会在 1051px 以上逼出无谓的内部横向滚动——.person-layout 在 1051–1500px 是两栏，
     .person-main 只有 673–942px。下限只负责挡住手机宽度把 9 列压成不可读，低于 760px 仍照旧滚。
     注意：这个 760px 是**横向滚动**的下限，与「表头不折行」不是同一件事。表头不折行要的是
     列宽尽量足（~952px，实测下限见下面的 @media 1011/939 两档），两者互不替代。
     内容装不下时浏览器优先折行、不优先滚动，所以 904px 以上靠上面的内边距档位保证不折行；
     904px 以下才折行——手机宽度，9 列本就只能折。给表头加 white-space: nowrap 能让表头
     一行放下，代价是在 1150px 上下逼出约 70px 的内部滚动（实测 tblW 842 > 容器 772），
     等于把刚去掉的问题请回来，所以不这么做，改用上面的 text-wrap: balance 把断点放匀。
     缺行情的记录显示「—」且不着色——缺值既不是涨也不是跌。 */
  .person-table { min-width: 760px; }
  /* 表头断行取均分，不取「填满第一行」。9 列表头里「交易日至今涨跌幅」「交易类型」都比列宽长，
     默认断行会把首行塞满、把最后一两个字甩到第二行（实测「交易日至今涨跌」98px + 「幅」14px）。
     均分后变成 4+4（56+56），列宽与表头高度都不变——只是看起来像被有意折的。
     不支持 text-wrap 的浏览器忽略这条，退回原来的断法，没有副作用。 */
  .person-table thead th { text-wrap: balance; } .person-table tbody tr { cursor: pointer; transition: background .15s ease; } .person-table tbody tr:hover { background: #faf7f0; } .table-return { font: 700 12px/1 var(--mono); }
  /* 披露后表现挪到交易明细披露之上、成为 .person-main 的首个子项后，它自带的
     margin-top 会和栅格间距叠加，把整列往下推一截。 */
  .person-main .performance-observation { margin-top: 0; } .person-security b { display: block; color: var(--text); font: 700 12px/1 var(--mono); } .person-security small { display: block; max-width: 170px; margin-top: 5px; overflow: hidden; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
  /* 这一块现在夹在标题区和交易明细表之间，是首屏里最占地方的一段非表格内容，
     行高与内边距按「三格数字 + 一行说明」压到最小；数字仍大于正文，级差还在。 */
  .performance-observation { margin-top: 18px; border: 1px solid var(--line); background: var(--panel); } .performance-observation-head { display: flex; align-items: center; gap: 9px; padding: 13px 18px 5px; } .performance-observation-head::before { content: ""; width: 3px; height: 16px; background: var(--signal); } .performance-observation-head h3 { margin: 0; font: 500 18px/1 var(--display); } .performance-observation-grid { display: grid; grid-template-columns: repeat(3,1fr); padding: 9px 18px 11px; } .performance-observation-stat { min-width: 0; padding-right: 14px; } .performance-observation-stat span { display: block; color: var(--muted); font-size: 10px; } .performance-observation-stat b { display: block; margin-top: 4px; font: 600 19px/1 var(--display); } .performance-observation-note { margin: 0 18px; padding: 9px 0 11px; border-top: 1px solid var(--line); color: var(--muted); font-size: 10px; line-height: 1.6; } .person-empty { padding: 44px 20px; color: var(--muted); text-align: center; font-size: 11px; } .person-page button:focus-visible, .stock-page button:focus-visible { outline: 2px solid var(--signal); outline-offset: 3px; }
  /* Readability scale: preserve display titles and priority names, enlarge supporting UI text. */
  .eyebrow { font-size: 12px; } .data-cutoff { font-size: 12px; line-height: 1.35; } .segmented button { font-size: 12px; } .section-head p { font-size: 14px; } .priority-card { min-height: 0; } .priority-kicker { font-size: 10px; } .priority-who { font-size: 16px; } .priority-role { font-size: 11.5px; } .flow-tag { font-size: 12px; } .flow-chip { font-size: 12.5px; } .chip-tip { font-size: 12px; } .flow-meta, .flow-empty, .flow-counts { font-size: 11px; } .window-tab { font-size: 16px; } .timeline-count { font-size: 11px; } .panel-title span { font-size: 12px; } .segmented.compact button { font-size: 12px; } .timeline-search span { font-size: 11px; } .filter-group button, .instrument-filter { height: 38px; font-size: 12px; } .timeline-row { min-height: 104px; } .who b { font-size: 15px; } .who small, .meta { font-size: 12px; line-height: 1.45; } .security { font-size: 17px; } .security small { font-size: 12px; } .amount { font-size: 13px; } .return { font-size: 21px; } .return small { font-size: 11px; } .timeline-empty, .hot-stock-empty, .source-empty, .stock-empty, .person-empty { font-size: 13px; } .hot-stock-row { min-height: 188px; } .hot-stock-table th { font-size: 12px; } .hot-stock-rank { font-size: 11px; } .hot-stock-ticker { font-size: 25px; } .hot-stock-company { font-size: 13px; } .hot-stock-attention b { font-size: 16px; } .hot-stock-attention span { font-size: 12px; } .hot-stock-signal { font-size: 10px; } .hs-side, .hs-figures, .hs-dates, .hs-empty { font-size: 11px; } .hs-amount { font-size: 13px; } .hot-stock-amount { font-size: 11px; } .hot-stock-return { font-size: 21px; } .hot-stock-return small { font-size: 10px; } .hot-stock-scope { font-size: 13px; } .balance-labels { font-size: 12px; } th { font-size: 12px; } td { font-size: 13px; line-height: 1.5; } .source-link { min-height: 108px; } .source-link-copy b { font-size: 15px; } .source-link-copy small, .source-link-action { font-size: 12px; } .footnote { color: var(--muted); font-size: 12px; } .drawer-role { font-size: 13px; } .drawer-callout b { font-size: 11px; } .drawer-callout p { font-size: 13px; } .fact span { font-size: 11px; } .fact b { font-size: 14px; } .direction-summary-head b { font-size: 13px; } .direction-summary-head span { font-size: 11px; } .stock-back, .person-back { font-size: 12px; } .stock-review-label, .person-review-label { font-size: 11px; } .stock-company { font-size: 15px; } .stock-cutoff { font-size: 11px; } .stock-status span { font-size: 11px; } .stock-metric span { font-size: 11px; } .stock-metric b { font-size: 18px; } .stock-metric small { font-size: 11px; } .stock-quarter-head { font-size: 12px; } .stock-quarter-item span { font-size: 11px; } .stock-quarter-note { font-size: 11px; } .stock-politician-intro p { font-size: 12px; } .stock-price-head span { font-size: 12px; } .stock-price-notes, .stock-page-note { font-size: 12px; } .stock-price-notes b { font-size: 13px; } .stock-table .stock-person b { font-size: 13px; } .stock-table .stock-person small { font-size: 11px; } .person-eyebrow { font-size: 12px; } .person-roleline span { font-size: 11px; } .person-source { font-size: 11px; } .person-card-head p { font-size: 11px; } .person-breakdown-copy { font-size: 12px; } .person-breakdown-copy b { font-size: 12px; } .person-note { font-size: 12px; } .person-note b { font-size: 11px; } .person-security b { font-size: 14px; } .person-security small { font-size: 11px; } .performance-observation-stat span, .performance-observation-note { font-size: 11px; }
  /* 下面两个断点按实测（Chromium 1600/1400/1200/1050/1001/1000/900/840/800/700/620/520/460/390/360
     全跑一遍，检查卡片内是否有元素 scrollWidth > clientWidth）定，不是估算：
     方向行改成「色块 + 标的紧挨着金额」之后，一行所需宽度变成
     色块 20 + 间距 9 + 标的 + 间距 10 + 金额，其中金额是最长的那一项（六位数区间约 190px）。
     三列时卡片宽 (视口-72)/3，1000px 视口下卡片 309px、方向行净宽 172px，金额已经放不下；
     再往下就会挤压金额，所以 1000px 退两列——两列时卡片 470px 起步，余量充足。
     两列在 620px 下卡片 280px、方向行净宽 172px 上下，再窄金额开始贴边，所以 620px 退单列。
     实测 360px 视口下单列仍无任何截断，因此不必再为手机宽度加断点。 */
  @media (max-width: 1000px) {
    .priority-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .consensus-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  } @media (max-width: 620px) {
    .priority-grid { grid-template-columns: 1fr; } .consensus-grid { grid-template-columns: 1fr; }
  }
  /* 热门股票表比别的断点早收 100px：它的「买入 / 卖出」一列要装下「标签 + 笔数 + 金额 + 日期」
     四栏，四条明细各一行，低于 1150px 这一列就被挤到日期截断，所以从这里就开始堆叠。
     表格退回堆叠后 thead 不隐藏——排序按钮就住在里面，藏掉等于窄屏没有排序入口——
     改成一条可换行的排序控件条；tbody 每行退回原来验证过的三栏 grid。 */
  @media (max-width: 1150px) {
    .hot-stock-table, .hot-stock-table thead, .hot-stock-table tbody, .hot-stock-table tr, .hot-stock-table th, .hot-stock-table td { display: block; } .hot-stock-table thead tr { display: flex; flex-wrap: wrap; align-items: center; gap: 0 2px; padding: 6px 20px 7px; border-bottom: 1px solid var(--line-strong); } .hot-stock-table th, .hot-stock-table td { width: auto; padding: 0; border: 0; } .hot-stock-table th:last-child { text-align: left; } .hot-stock-return-head small { display: none; } .hs-sort { margin: 0; }
    /* align-items: center 让跨两行的标的一栏在整行里居中：表格布局里本来靠 td 的
       vertical-align 居中，退回 grid 后要显式写回来，否则标的会贴在行顶。 */
    .hot-stock-table tbody tr { display: grid; height: auto; align-items: center; grid-template-columns: minmax(160px,.8fr) minmax(190px,1fr) 92px; grid-template-areas: "identity people return" "identity flow flow"; row-gap: 15px; column-gap: 22px; padding: 19px 20px; border-bottom: 1px solid var(--line); } .hot-stock-table tbody tr:last-child { border-bottom: 0; } .hot-stock-table td.hot-stock-empty { grid-column: 1 / -1; } .hs-cell-identity { grid-area: identity; } .hs-cell-people { grid-area: people; } .hs-cell-flow { grid-area: flow; } .hs-cell-return { grid-area: return; }
  }
  /* .person-layout 比 .person-hero-grid 早一步塌成单栏（1280 而不是 1050）。原因只有一个：
     9 列明细表要 ~952px 才不折行，而两栏布局在 1280 以下给不出这个宽度——实测主栏从
     1280px 视口的 880px 一路掉到 1060px 视口的 684px，右栏再窄也补不上这个缺口。
     塌成单栏后主栏就是整个 shell：1280px 视口下 1232px、1050px 下 1003px，都在 952 以上。
     只提前 .person-layout：标题区的两栏到 1050px 才塌，那里不受这个宽度约束。
     ~952 是**逐行**量出来的（975 是只量首行得到的偏小值，首行没有最长的公司名；反过来
     193px 是未截断的 "Lockheed Martin Corporation"，而 .person-security small 本来就有
     170px 上限 + 省略号，所以真实需求介于两者之间）。折行与否用 Range 数行盒判定，
     不能用 th 高度阈值：任一个表头折行会把整行 thead 抬到 49px，其余 8 个没折的也一样高，
     高度判据会把它们全部误报成折行。
     单栏之后 shell 宽度 = 视口 - 50，所以 16px 内边距下的不折行下限是 1012px 视口——
     960 / 1000 / 1024 这些常见的半屏窗口都够不着。下面两档只收窄单元格左右内边距
     （9 列 × 2 边 = 18 条边，每收 1px 就省 18px），把下限压到 904px 视口：
     12px → 940px 视口，10px → 904px 视口，两档首尾相接、中间没有断档。
     不改内容、不加横向滚动，也**不动公司名的截断上限**——那会真的藏掉文字（实测 110px
     上限会截断 2 个公司名），而收内边距一个字节都不藏。纵向内边距保持原样，行高不变。
     904px 以下回到折行：那是手机宽度，9 列在这个宽度里本就只能折，横向滚动更糟。 */
  @media (max-width: 1011px) {
    .person-table th, .person-table td { padding-left: 12px; padding-right: 12px; }
  } @media (max-width: 939px) {
    .person-table th, .person-table td { padding-left: 10px; padding-right: 10px; }
  } @media (max-width: 1280px) {
    .person-layout { grid-template-columns: 1fr; }
  } @media (max-width: 1050px) {
    .stock-heading { grid-template-columns: 1fr; } .stock-quarter-grid { grid-template-columns: repeat(3,1fr); } .person-hero-grid { grid-template-columns: 1fr; }
    /* 单列后肖像独占整行，300px 的高度会白占一屏，所以缩到 240px。 */
    .person-portrait { min-height: 240px; }
  } @media (max-width: 720px) {
    .shell { width: min(100% - 24px, 1500px); } .masthead { align-items: flex-start; flex-direction: column; padding: 20px 0; }
    /* 窄屏的停靠栏不能再按两行排：页首那版竖排的标题 + 两行右侧信息，
       吸附下来会占掉手机屏幕的近四分之一，浏览时反而挡路。
       吸附态强制压回一行，只留标题与 30/90，标题超宽就省略。 */
    .masthead.is-stuck { flex-direction: row; align-items: center; gap: 12px; padding: 8px 12px; } .masthead.is-stuck .header-side { width: auto; } .masthead.is-stuck .header-actions { width: auto; } .masthead.is-stuck .header-meta { display: none; } .masthead.is-stuck .brand { min-width: 0; gap: 9px; } .masthead.is-stuck .brand > div { min-width: 0; } .masthead.is-stuck .brand h1 { font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; } .masthead.is-stuck .segmented button { padding: 9px 13px; font-size: 12.5px; } .header-side { width: 100%; min-width: 0; justify-items: stretch; } .header-meta { min-width: 0; flex-wrap: wrap; justify-content: flex-start; } .data-cutoff { min-width: 0; text-align: left; white-space: normal; overflow-wrap: anywhere; font-size: 12px; line-height: 1.45; } .header-actions { width: 100%; justify-content: flex-start; } .priority-card { min-height: 0; } .window-tab { min-height: 46px; padding: 0 15px; } .hot-stock-table thead tr { padding: 6px 16px 7px; } .window-block-title { font-size: 20px; } .hot-stock-table tbody tr { grid-template-columns: minmax(0,1fr) auto; grid-template-areas: "identity return" "people people" "flow flow"; gap: 15px 18px; padding: 20px 16px 22px; } .hot-stock-identity { grid-template-columns: 24px minmax(0,1fr); gap: 8px; } .hot-stock-company { max-width: 180px; } .hot-stock-people { display: flex; align-items: center; justify-content: space-between; gap: 14px; } .hot-stock-avatar-stack { flex: 0 0 auto; margin-top: 0; }
    /* 窄屏放不下「标签 笔数 金额 日期」四栏，日期回落到第二行。 */
    .hot-stock-flow-row { grid-template-columns: 28px minmax(56px, auto) minmax(0, 1fr); column-gap: 8px; row-gap: 3px; } .hs-amount { text-align: right; } .hs-dates { grid-column: 2 / -1; text-align: left; } .hot-stock-amount { width: 100%; text-align: left; } .timeline-row { grid-template-columns: 42px 1fr 84px; padding: 15px 0; } .timeline-row .security { grid-column: 2; } .timeline-row .trade-block { grid-column: 2; } .timeline-row .return { grid-column: 3; grid-row: 1 / span 3; } .panel-title { align-items: flex-start; } .timeline-toolbar { grid-template-columns: 1fr; } .filter-group { width: 100%; } .filter-group button { flex: 1; } .instrument-filter { width: 100%; } .source-list { grid-template-columns: 1fr; } .stock-shell { width: min(100% - 24px, 1420px); } .stock-nav { align-items: flex-start; flex-direction: column; padding: 14px 0; } .stock-nav-actions { width: 100%; justify-content: space-between; } .stock-review-label { font-size: 11px; } .stock-heading { gap: 26px; } .stock-id { align-items: flex-start; } .stock-glyph { width: 52px; height: 52px; font-size: 18px; }
    /* 三格在 720px 以下退两列：360px 视口下三列只剩 81px 净宽，头像堆叠和
       「$880K–$1.9M」都会溢出。 */
    .stock-metrics { grid-template-columns: repeat(2,1fr); } .stock-metric { border-top: 1px solid var(--line); } .stock-metric:nth-child(odd) { border-right: 1px solid var(--line); } .stock-metric:nth-child(even) { border-right: 0; }
    /* 退两列后第三格独占第二行，右半边会空出一格带竖线的空位。让它跨满整行，
       与 :nth-child(odd) 同为 (0,2,0)，靠源码顺序在后取胜。 */
    .stock-metric:last-child { grid-column: 1 / -1; border-right: 0; } .stock-metric b { font-size: 20px; white-space: normal; overflow-wrap: anywhere; } .stock-quarter-grid { grid-template-columns: 1fr; } .stock-quarter-item + .stock-quarter-item { border-top: 1px solid var(--line); } .stock-politician-intro { align-items: flex-start; flex-direction: column; } .stock-price-head { align-items: flex-start; flex-direction: column; gap: 7px; } .stock-price-frame { padding: 10px 8px 8px; } .stock-price-chart { width: 720px; min-height: 230px; } .person-shell { width: min(100% - 24px, 1320px); } .person-nav { align-items: flex-start; flex-direction: column; padding: 14px 0; } .person-nav-actions { width: 100%; justify-content: space-between; } .person-review-label { font-size: 11px; } .person-hero { padding-top: 18px; } .person-hero-grid { gap: 18px; }
    /* 肖像高度不在这里再收一档，沿用 1050px 断点的 240px。手机单列后肖像独占整行、
       宽高比拉得很扁（390px 视口下 366×200），演示数据里的肖像是 SVG，内层会按自身宽高比
       留边——扁盒子会把两侧的底色露得更多。240px 是「压过」和「不难看」的折中。 */
    .person-name { font-size: clamp(30px,9vw,42px); } .person-card-head { align-items: flex-start; flex-direction: column; } .performance-observation-grid { grid-template-columns: 1fr; gap: 18px; } .performance-observation-stat { padding-right: 0; }
  } @media (max-width: 520px) {
    .priority-avatar { width: 22px; height: 22px; }
  }
</style>
</head>
<body>
  <main class="shell">
    <!-- 1px 哨兵：它离开视口顶端的那一刻，就是停靠栏开始吸附的那一刻。
         用一个独立元素而不是监听 .masthead 自己的位置，是因为停靠栏吸附后仍在视口里，
         观察它本身分不出「还没吸」和「已经吸住」两种状态。 -->
    <div class="dock-sentinel" aria-hidden="true"></div>
    <header class="masthead">
        <div class="brand"><span class="brand-mark"></span><div><div class="eyebrow">U.S. political disclosures</div><h1>White House Stock Tracker</h1></div></div>
      <div class="header-side">
        <div class="header-meta">__DEMO_BADGE__<div class="data-cutoff" id="cutoff"></div></div>
        <div class="header-actions">
          <div class="segmented" aria-label="跳转到时间窗口区块"><button data-window-anchor="30" class="active">30 天</button><button data-window-anchor="90">90 天</button></div>
        </div>
      </div>
    </header>

    <div class="section-head"><h3>国会山重点关注</h3><p>近 30 天买卖标的</p></div>
    <section class="priority-grid" id="priorityGrid"></section>

    <!-- 买入共识：每个窗口一块，两块同时呈现，与下方表格区块一致。
         这里刻意不用 data-window-block——顶部 30/90 锚点靠 querySelector 取首个匹配，
         用了会让锚点跳到共识块而不是表格。共识块不参与顶部锚点。 -->
    <div class="section-head"><h3>国会山买入共识</h3><p>近 30 / 90 天买入金额最高的 6 只标的 · 金额为披露区间</p></div>
    <section class="consensus-block" data-consensus-window="30">
      <div class="window-block-head"><h4 class="window-block-title">30 天窗口</h4></div>
      <div class="consensus-grid" id="consensusGrid30"></div>
    </section>
    <section class="consensus-block" data-consensus-window="90">
      <div class="window-block-head"><h4 class="window-block-title">90 天窗口</h4></div>
      <div class="consensus-grid" id="consensusGrid90"></div>
    </section>

    <section class="dashboard-explorer">
      <!-- 每个窗口块 = 大标题（表格外）+ 一条自己的视图切换条 + 一张表。
           切换条每块各一条但联动，筛选控件每块各一套且互不影响。 -->
      <div class="window-block" id="window-30" data-window-block="30">
        <div class="window-block-head"><h4 class="window-block-title">30 天窗口</h4></div>
        <div class="panel">
          <nav class="window-tabs" role="tablist" aria-label="30 天窗口的数据视图">
            <button class="window-tab active" id="window30TabHot" type="button" role="tab" aria-selected="true" aria-controls="window30Hot" data-window-view="hot-stocks">国会山热门关注股票</button>
            <button class="window-tab" id="window30TabTimeline" type="button" role="tab" aria-selected="false" aria-controls="window30Timeline" tabindex="-1" data-window-view="timeline">标的交易明细</button>
          </nav>
          <div class="window-view" id="window30Hot" role="tabpanel" aria-labelledby="window30TabHot" data-window-panel="hot-stocks">
            <!-- 表头内容不变，只是搬进真的 <thead>，让每个表头单元格挂一枚排序按钮 -->
            <div class="hot-stock-scope" id="hotStockReturnScope30"></div>
            <table class="hot-stock-table" id="hotStockTable30">
              <thead>
                <tr>
                  <th scope="col" aria-sort="none"><button class="hs-sort" type="button" data-hs-sort="ticker">股票 / 公司</button></th>
                  <th scope="col" aria-sort="descending"><button class="hs-sort active desc" type="button" data-hs-sort="people">关联人物</button></th>
                  <th scope="col" aria-sort="none"><button class="hs-sort" type="button" data-hs-sort="amount">买入 / 卖出</button></th>
                  <th scope="col" class="hot-stock-return-head" aria-sort="none"><button class="hs-sort" type="button" data-hs-sort="return">股价涨跌</button><small>窗口首笔交易日 → 行情截止日</small></th>
                </tr>
              </thead>
              <tbody id="hotStocks30"></tbody>
            </table>
          </div>
          <div class="window-view" id="window30Timeline" role="tabpanel" aria-labelledby="window30TabTimeline" data-window-panel="timeline" hidden>
            <div class="timeline-toolbar">
              <label class="timeline-search"><span>SEARCH</span><input id="timelineSearch30" type="search" placeholder="人物、股票代码或公司"></label>
              <div class="filter-group" aria-label="交易方向"><button class="active" data-tx-filter="all">全部</button><button data-tx-filter="purchase">买入</button><button data-tx-filter="sale">卖出</button></div>
              <select class="instrument-filter" id="instrumentFilter30" aria-label="交易类型"><option value="all">全部类型</option><option value="Stock">股票</option><option value="Option">期权</option><option value="ETF">ETF</option></select>
              <span class="timeline-count" id="timelineCount30"></span>
            </div>
            <div class="timeline" id="timeline30"></div>
          </div>
        </div>
      </div>
      <div class="window-block" id="window-90" data-window-block="90">
        <div class="window-block-head"><h4 class="window-block-title">90 天窗口</h4></div>
        <div class="panel">
          <nav class="window-tabs" role="tablist" aria-label="90 天窗口的数据视图">
            <button class="window-tab active" id="window90TabHot" type="button" role="tab" aria-selected="true" aria-controls="window90Hot" data-window-view="hot-stocks">国会山热门关注股票</button>
            <button class="window-tab" id="window90TabTimeline" type="button" role="tab" aria-selected="false" aria-controls="window90Timeline" tabindex="-1" data-window-view="timeline">标的交易明细</button>
          </nav>
          <div class="window-view" id="window90Hot" role="tabpanel" aria-labelledby="window90TabHot" data-window-panel="hot-stocks">
            <div class="hot-stock-scope" id="hotStockReturnScope90"></div>
            <table class="hot-stock-table" id="hotStockTable90">
              <thead>
                <tr>
                  <th scope="col" aria-sort="none"><button class="hs-sort" type="button" data-hs-sort="ticker">股票 / 公司</button></th>
                  <th scope="col" aria-sort="descending"><button class="hs-sort active desc" type="button" data-hs-sort="people">关联人物</button></th>
                  <th scope="col" aria-sort="none"><button class="hs-sort" type="button" data-hs-sort="amount">买入 / 卖出</button></th>
                  <th scope="col" class="hot-stock-return-head" aria-sort="none"><button class="hs-sort" type="button" data-hs-sort="return">股价涨跌</button><small>窗口首笔交易日 → 行情截止日</small></th>
                </tr>
              </thead>
              <tbody id="hotStocks90"></tbody>
            </table>
          </div>
          <div class="window-view" id="window90Timeline" role="tabpanel" aria-labelledby="window90TabTimeline" data-window-panel="timeline" hidden>
            <div class="timeline-toolbar">
              <label class="timeline-search"><span>SEARCH</span><input id="timelineSearch90" type="search" placeholder="人物、股票代码或公司"></label>
              <div class="filter-group" aria-label="交易方向"><button class="active" data-tx-filter="all">全部</button><button data-tx-filter="purchase">买入</button><button data-tx-filter="sale">卖出</button></div>
              <select class="instrument-filter" id="instrumentFilter90" aria-label="交易类型"><option value="all">全部类型</option><option value="Stock">股票</option><option value="Option">期权</option><option value="ETF">ETF</option></select>
              <span class="timeline-count" id="timelineCount90"></span>
            </div>
            <div class="timeline" id="timeline90"></div>
          </div>
        </div>
      </div>
    </section>

    <section class="secondary">
      <div class="panel">
        <div class="panel-title"><h3>最新一期数据源</h3></div>
        <div class="source-list" id="sourceLinks"></div>
      </div>
    </section>

    <footer class="footnote">__FOOTNOTE__</footer>
  </main>

  <section class="stock-page" id="stockPage" aria-hidden="true">
    <div class="stock-shell">
      <header class="stock-nav">
        <button class="stock-back" id="stockBack">← 返回全局看板</button>
        <div class="stock-nav-actions"><span class="stock-review-label">__BUILD_LABEL__</span><div class="segmented compact" aria-label="单股看板时间窗口"><button data-stock-window="30">30 天</button><button data-stock-window="90">90 天</button></div></div>
      </header>
      <div id="stockPageContent"></div>
    </div>
  </section>

  <section class="person-page" id="personPage" aria-hidden="true">
    <div class="person-shell">
      <header class="person-nav">
        <button class="person-back" id="personBack">← 返回全局看板</button>
        <div class="person-nav-actions"><span class="person-review-label">__BUILD_LABEL__</span><div class="segmented compact" aria-label="人物主页时间窗口"><button data-person-window="30">30 天</button><button data-person-window="90">90 天</button></div></div>
      </header>
      <div id="personPageContent"></div>
    </div>
  </section>

  <div class="drawer-backdrop" id="drawerBackdrop"></div>
  <aside class="drawer" id="drawer" aria-hidden="true"><button class="drawer-close" id="drawerClose" aria-label="关闭">×</button><div id="drawerContent"></div></aside>

<script>
const DATA = __DATA__;
const IS_DEMO = Boolean(DATA.meta.is_demo);
const PEOPLE = Object.fromEntries(DATA.people.map(p => [p.id, p]));
// 看板顶部指标与人物卡固定 30 天窗口，不随顶部 30/90 锚点按钮变化。
const DASHBOARD_WINDOW = 30;
// 同时呈现的两个窗口区块（30 天块在上、90 天块在下）。
const DASHBOARD_WINDOWS = [30, 90];
// 人物卡一个方向最多直接列出几只标的，其余收进「+N」。3 枚之后 chip 开始折行，
// 卡片高度会被撑得参差；被折起来的标的并不会消失——「+N」悬停时仍然列出它们各自的金额。
const FLOW_CHIP_LIMIT = 3;
// 单股页「关联政客 / 官员」一格最多铺几枚头像，其余收进 +N。
const STOCK_AVATAR_LIMIT = 5;
// 买入共识板块每个窗口最多几张卡。
const CONSENSUS_LIMIT = 6;
// activeWindow 只服务于人物页／单股页的 30/90 筛选按钮（那里仍是真实筛选器）。
let activeWindow = Number(DATA.meta.default_window_days || 30);
// 视图（hot-stocks / timeline）是两个窗口块共用的一份状态：两条 Tab 条联动，
// 点任意一条两块一起切，同一屏里两个窗口永远显示同一种视图，不会一块看股票、一块看明细。
let dashboardView = 'hot-stocks';
// 筛选反过来是每块各自一份：两块表各筛各的，在 30 天里搜一个代码不该把 90 天那块也筛空。
const timelineFilters = {
  30: {query: '', direction: 'all', instrument: 'all'},
  90: {query: '', direction: 'all', instrument: 'all'}
};
let activeStockTicker = null;
let stockDirection = 'all';
// 单股页政客与价格合并为一页后没有 tab 状态；人物页同理（画像已并入交易明细）。
// personDirection / stockDirection 仍是表格内的方向筛选，与页签无关。
let activePersonId = null;
let personDirection = 'all';
let returnToPersonId = null;

const yuan = new Intl.NumberFormat('en-US');
const money = n => '$' + yuan.format(n);
const range = t => `${money(t.amount_low)}–${money(t.amount_high)}`;
// 占位符（破折号）不算有效值：人物卡与买入共识卡都靠它决定某个字段该不该占位置。
// 提到模块作用域是因为它现在有两个消费者，不再只是 renderPriority 的内部实现。
const hasDisplayValue = value => value && !['—', '-', '–'].includes(String(value).trim());
// 交易日与申报日必须按记录自身的日历日呈现，不能按浏览者本地时区换算。
// 记录里的日期串已经是申报机构所在时区的日历日：交易日是纯日期「2026-08-28」，
// 申报时间是带偏移的时间戳「2026-09-13T22:16:00-04:00」。两者都只取字面年月日。
// 走 new Date() 换算会错位：后者在 UTC+8 会显示成「9月14日」，前者在 UTC-5 会显示成「8月27日」，
// 而 SKILL.md 要求交易日、申报日必须与披露记录逐字一致。
const dateLabel = value => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value == null ? '' : value));
  return m ? `${+m[2]}月${+m[3]}日` : String(value == null ? '' : value);
};
const txLabel = t => t.transaction_type === 'purchase' ? '买入' : t.transaction_type === 'sale' ? '卖出' : '其他';
const txClass = t => t.transaction_type === 'purchase' ? 'buy' : t.transaction_type === 'sale' ? 'sell' : 'other';
// 只有有限数值才判涨跌方向：NaN >= 0 为 false，会把无行情的「—」误染成红色跌。
// null/undefined/'' 也要先挡掉，因为 Number(null) === 0 会被当成上涨染绿。
const returnClass = n => {
  if (n === null || n === undefined || n === '') return '';
  const value = Number(n);
  return Number.isFinite(value) ? (value >= 0 ? 'positive' : 'negative') : '';
};
// 与 returnClass 同一套空值判据：没有行情序列时 underlying_return_* 是 null，
// 直接 n.toFixed 会抛错。缺值一律显示「—」，既不着色也不当 0。
const pct = n => {
  if (n === null || n === undefined || n === '') return '—';
  const value = Number(n);
  return Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` : '—';
};
const instrument = t => t.instrument_type === 'Option' ? `${t.option_type} · $${t.strike_price} · ${t.expiration_date}` : t.instrument_type;
const partyLabel = party => ({R:'共和党', D:'民主党', I:'独立'}[party] || party);
const ownerLabel = owner => ({Self:'本人', Spouse:'配偶', Joint:'共同'}[owner] || owner);

// 窗口必填：看板区块直接传 30/90，人物页与单股页传 activeWindow。
function windowTransactions(days) {
  const endDate = DATA.meta.data_cutoff_at.slice(0, 10);
  const start = new Date(`${endDate}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  const startDate = start.toISOString().slice(0, 10);
  return DATA.transactions.filter(t => t.transaction_date >= startDate && t.transaction_date <= endDate)
    .sort((a,b) => new Date(b.filed_at) - new Date(a.filed_at));
}

function windowSummary(txs) {
  const lags = txs.map(t => t.disclosure_lag_days).sort((a,b) => a-b);
  const median = lags.length ? lags[Math.floor(lags.length / 2)] : 0;
  return {
    people: new Set(txs.map(t => t.person_id)).size,
    buys: txs.filter(t => t.transaction_type === 'purchase').length,
    sells: txs.filter(t => t.transaction_type === 'sale').length,
    low: txs.reduce((n,t) => n + t.amount_low, 0),
    high: txs.reduce((n,t) => n + t.amount_high, 0),
    median
  };
}

function directionStats(txs) {
  const buys = txs.filter(t => t.transaction_type === 'purchase');
  const sells = txs.filter(t => t.transaction_type === 'sale');
  const others = txs.filter(t => !['purchase', 'sale'].includes(t.transaction_type));
  const total = Math.max(buys.length + sells.length + others.length, 1);
  return { buys, sells, others, total, buyPct: buys.length / total * 100, sellPct: sells.length / total * 100, otherPct: others.length / total * 100 };
}

// 代表记录：最新一笔交易日，并列再比申报时间。ISO 字符串可直接比较。
function latestOf(txs) {
  return txs.reduce((best, t) => (!best || `${t.transaction_date}${t.filed_at}` > `${best.transaction_date}${best.filed_at}`) ? t : best, null);
}

// 分方向聚合：笔数、去重人数、上下限「分别」求和（不是净额），以及最新一笔的交易日与申报日。
function directionAggregate(txs, direction) {
  const items = txs.filter(t => t.transaction_type === direction);
  if (!items.length) return null;
  const latest = latestOf(items);
  return {
    count: items.length,
    people: new Set(items.map(t => t.person_id)).size,
    low: items.reduce((n, t) => n + Number(t.amount_low || 0), 0),
    high: items.reduce((n, t) => n + Number(t.amount_high || 0), 0),
    tradeDate: latest.transaction_date,
    filedAt: latest.filed_at
  };
}

// 某人窗口内某方向的「全部」标的，按披露金额上限合计降序；并列依次比下限、笔数、代码升序。
// 返回全量数组，不在这里截断——卡片显示几枚是版式问题，由渲染层决定。
// 与 directionAggregate 一样是描述性统计，不代表推荐、评分或交易信号。
function securitiesByAmount(txs, personId, direction) {
  const groups = new Map();
  txs.forEach(t => {
    if (t.person_id !== personId || t.transaction_type !== direction || !t.ticker) return;
    if (!groups.has(t.ticker)) groups.set(t.ticker, []);
    groups.get(t.ticker).push(t);
  });
  return [...groups].map(([ticker, items]) => ({...directionAggregate(items, direction), ticker}))
    .sort((a, b) => b.high - a.high || b.low - a.low || b.count - a.count || a.ticker.localeCompare(b.ticker));
}

// 窗口内「买入披露金额上限合计」最高的标的，每只带买入金额最高的前三位人物。
// 刻意不设买家数门槛：一只股票只要买入金额最大就入选，哪怕只有一个买家——
// 门槛是产品判断，藏在聚合里会让「为什么这只没出现」变得无法从界面推断。
// 这是描述性统计，不代表推荐、评分或交易信号。
function buyConsensus(txs, limit) {
  const groups = new Map();
  txs.forEach(t => {
    if (t.transaction_type !== 'purchase' || !t.ticker) return;
    if (!groups.has(t.ticker)) groups.set(t.ticker, []);
    groups.get(t.ticker).push(t);
  });
  return [...groups].map(([ticker, items]) => {
    const byPerson = new Map();
    items.forEach(t => {
      if (!byPerson.has(t.person_id)) byPerson.set(t.person_id, []);
      byPerson.get(t.person_id).push(t);
    });
    // 每个人在这只股票上的买入聚合（上下限分别求和 + 笔数），排序键与标的排序同一套，
    // 并列时才用 personId 兜底，保证同一份快照每次渲染出同样的前三名。
    const buyers = [...byPerson].map(([personId, rows]) => ({personId, ...directionAggregate(rows, 'purchase')}))
      .sort((a, b) => b.high - a.high || b.low - a.low || b.count - a.count || a.personId.localeCompare(b.personId));
    // 公司名只能从交易行取（市场行情投影里没有 company 字段）。
    return {ticker, company: items[0].asset_name, buyers, buyerCount: byPerson.size, agg: directionAggregate(items, 'purchase')};
  }).sort((a, b) => b.agg.high - a.agg.high || b.agg.low - a.agg.low || b.agg.count - a.agg.count || a.ticker.localeCompare(b.ticker))
    .slice(0, limit);
}function compactMoney(n) {
  if (n >= 1000000) return '$' + (n / 1000000).toFixed(n % 1000000 ? 1 : 0) + 'M';
  return '$' + Math.round(n / 1000) + 'K';
}

const priceMoney = n => Number.isFinite(Number(n)) ? '$' + Number(n).toFixed(2) : '—';
const pricePct = n => Number.isFinite(Number(n)) ? `${Number(n) >= 0 ? '+' : ''}${Number(n).toFixed(2)}%` : '—';

function quarterInfo(value) {
  const date = String(value || '').slice(0,10);
  const year = Number(date.slice(0,4));
  const month = Number(date.slice(5,7));
  const quarter = Math.floor((month - 1) / 3) + 1;
  const startMonth = String((quarter - 1) * 3 + 1).padStart(2,'0');
  return { label: `${year} Q${quarter}（${date.slice(5)}）`, start: `${year}-${startMonth}-01`, end: date };
}

function buildSyntheticDailyPrices(market) {
  if (market && Array.isArray(market.price_history) && market.price_history.length > 1) return market.price_history;
  if (!IS_DEMO) return [];
  if (!market || !Number.isFinite(Number(market.current_price)) || !Number.isFinite(Number(market.previous_quarter_end_price))) return [];
  const startDate = new Date(`${market.history_start_date}T12:00:00Z`);
  const referenceDate = new Date(`${market.previous_quarter_end}T12:00:00Z`);
  const endDate = new Date(`${market.as_of_date}T12:00:00Z`);
  const startPrice = Number(market.history_start_price || market.previous_quarter_end_price);
  const referencePrice = Number(market.previous_quarter_end_price);
  const endPrice = Number(market.current_price);
  const seed = [...market.ticker].reduce((sum,char) => sum + char.charCodeAt(0), 0);
  const days = [];
  for (let cursor = new Date(startDate); cursor <= endDate; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    if (![0,6].includes(cursor.getUTCDay())) days.push(new Date(cursor));
  }
  return days.map((date,index) => {
    const beforeReference = date <= referenceDate;
    const segmentStart = beforeReference ? startDate : referenceDate;
    const segmentEnd = beforeReference ? referenceDate : endDate;
    const segmentStartPrice = beforeReference ? startPrice : referencePrice;
    const segmentEndPrice = beforeReference ? referencePrice : endPrice;
    const span = Math.max(segmentEnd - segmentStart, 1);
    const progress = Math.min(1, Math.max(0, (date - segmentStart) / span));
    const bridge = segmentStartPrice + (segmentEndPrice - segmentStartPrice) * progress;
    const envelope = Math.sin(Math.PI * progress);
    const wave = Math.sin((index + seed) * .19) * .032 + Math.sin((index + seed * .7) * .053) * .052 + Math.sin((index + seed * 1.3) * .011) * .035;
    return { date: date.toISOString().slice(0,10), close: Math.max(.01, bridge * (1 + wave * envelope)) };
  });
}

// 曲线上的头像标记要把「鼠标停在哪个点上」换算回日线索引，图表几何因此挂在模块级变量上，
// 由下面绑在 document 上的那条 mousemove 读。每次重渲重算；没有序列时清空，免得浮层
// 拿着上一次的几何继续报数。
let priceChartGeo = null;

// 交易日在日线序列上找最近的一点。序列是连续的日线，直接线性扫即可（约 730 点，单只票
// 最多十几笔），不用先排序再二分。离两端任意一端超过 45 天的记录返回 -1：那已经不是
// 「最近的点」，把 24 个月窗口外的交易钉在图表边缘，会读成它就发生在边缘那一天。
function nearestSeriesIndex(series, date) {
  const target = new Date(date).getTime();
  if (!Number.isFinite(target)) return -1;
  let best = 0, bestGap = Infinity;
  for (let i = 0; i < series.length; i++) {
    const gap = Math.abs(new Date(series[i].date).getTime() - target);
    if (gap < bestGap) { bestGap = gap; best = i; }
  }
  return bestGap > 3888000000 ? -1 : best;
}

/* 曲线上的买卖人标记：买在上、卖在下，各自沿垂直方向分行。
   yOf 收的是「序列下标 → 该点在图上的 y」，不是价格——曲线本身是 y(close)，
   标记也必须落在同一条线上，所以由调用方把下标换算好再传进来。
   24 个月里同一只票的交易常常挤在最近几天——实测 demo 里 8 笔 NVDA 落在 19px 之内——
   横向根本排不开，只能叠起来。所以同一方向内按 x 从左到右贪心分行：离这一行最后一个标记
   够远才放得进这一行，够不着就换下一行；超过 MAX_ROWS 的收进一枚 +N，+N 的浮层逐个列出
   被折叠的人。折叠可以减少同时露出的头像，但不能让任何一笔变得不可达。 */
function priceMarkSlots(series, txs, xOf, yOf, top, plotHeight) {
  // PITCH 是行距，必须大于标记的外径（含描边的 2×(R+2)）才留得出缝：22 时外径正好等于
  // 行距，相邻两枚的圈**相切**，一屏看过去像叠在一起。28 留出 6px 的空隙。
  const R = 10, PITCH = 28, MAX_ROWS = 4;
  const GAP = 10;          // 买块底边与卖块顶边之间的最小净空
  const dated = txs.map(t => ({t, index: nearestSeriesIndex(series, t.transaction_date)}))
    .filter(item => item.index >= 0)
    .sort((a, b) => a.index - b.index);
  const slots = [];
  const people = {buy: new Set(), sell: new Set()};
  [['purchase', 'buy'], ['sale', 'sell']].forEach(([type, kind]) => {
    const rowEnds = [];
    const placed = [];
    const folded = [];
    dated.filter(item => item.t.transaction_type === type).forEach(item => {
      people[kind].add(item.t.person_id);
      const px = xOf(item.index);
      let row = rowEnds.findIndex(end => px - end >= R * 2 + 4);
      if (row === -1) {
        if (rowEnds.length >= MAX_ROWS) { folded.push({...item, px}); return; }
        rowEnds.push(px); row = rowEnds.length - 1;
      } else rowEnds[row] = px;
      placed.push({...item, kind, row, px});
    });
    // 同一方向的全部标记**共用一个基线**，而不是各自从自己的曲线点起算。各自起算时
    // 「买在上、卖在下」只对单笔成立：一个卖点的价格高于某个买点时，往下排的卖标记就会
    // 落进往上排的买标记里，两种颜色的圈交叉在一起，反而读不出谁买谁卖。
    // 基线取该方向最靠外的那一个点（买取最高的点，卖取最低的点），于是所有买标记都在
    // 每一个买点之上、所有卖标记都在每一个卖点之下，两个方向不再互相穿插。
    // row 0 的头像外缘离基线 6px，每往外一行加一个 PITCH。引线仍连到各自的真点，
    // 所以行的远近只是排布，位置本身不会说谎。
    const ys = placed.map(slot => yOf(slot.index));
    const base = ys.length ? (kind === 'buy' ? Math.min(...ys) : Math.max(...ys)) : 0;
    placed.forEach(slot => { slot.py = yOf(slot.index); slot.cy = base + (kind === 'buy' ? -1 : 1) * (R + 6 + slot.row * PITCH); });
    if (folded.length) {
      const row = Math.min(rowEnds.length, MAX_ROWS);
      const px = folded.reduce((sum, item) => sum + item.px, 0) / folded.length;
      const py = folded.reduce((sum, item) => sum + yOf(item.index), 0) / folded.length;
      placed.push({kind, row, px, py, cy: base + (kind === 'buy' ? -1 : 1) * (R + 6 + row * PITCH), folded: folded.map(item => item.t)});
    }
    slots.push(...placed);
  });
  // 共基线只保证「买都在买点之上、卖都在卖点之下」，两个方向之间仍可能贴住——例如价格一路
  // 上涨时，最低的卖点可能高于最高的买点。这里再把卖块整体下移到买块底边之下，方向的分栏才
  // 是硬保证而不是通常成立。
  const buySlots = slots.filter(slot => slot.kind === 'buy');
  const sellSlots = slots.filter(slot => slot.kind === 'sell');
  if (buySlots.length && sellSlots.length) {
    const buyBottom = Math.max(...buySlots.map(slot => slot.cy + R));
    const sellTop = Math.min(...sellSlots.map(slot => slot.cy - R));
    if (sellTop < buyBottom + GAP) sellSlots.forEach(slot => { slot.cy += buyBottom + GAP - sellTop; });
  }
  // 整条标记带越出绘图区时整体平移，而不是逐个夹紧：逐个夹紧会把好几行压到同一个 y 上，
  // 头像就叠在一起了，而分行本来就是为了不叠。平移之后引线仍然指向曲线上的真点。
  // 顺序有讲究：先修买块（向上排，最容易顶出上边），再修卖块，且卖块的平移量要避开买块，
  // 否则刚分开的两栏会被夹紧重新压到一起。
  ['buy', 'sell'].forEach(kind => {
    const group = slots.filter(slot => slot.kind === kind);
    if (!group.length) return;
    const cys = group.map(slot => slot.cy);
    const overflow = kind === 'buy' ? (Math.min(...cys) - R) - top : (Math.max(...cys) + R) - (top + plotHeight);
    if (kind === 'buy' ? overflow < 0 : overflow > 0) group.forEach(slot => { slot.cy -= overflow; });
  });
  if (buySlots.length && sellSlots.length) {
    const buyBottom = Math.max(...buySlots.map(slot => slot.cy + R));
    const sellTop = Math.min(...sellSlots.map(slot => slot.cy - R));
    if (sellTop < buyBottom + GAP) sellSlots.forEach(slot => { slot.cy += buyBottom + GAP - sellTop; });
  }
  slots.forEach((slot, index) => { slot.id = index; });
  return {slots, hidden: txs.length - dated.length, buyPeople: people.buy.size, sellPeople: people.sell.size};
}

function priceChart(market, txs) {
  const series = buildSyntheticDailyPrices(market);
  if (series.length < 2) { priceChartGeo = null; return '<div class="stock-price-empty">当前快照没有可展示的行情序列。</div>'; }
  const width = 1120, height = 340, left = 66, right = 54, top = 20, bottom = 44;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const values = series.map(point => point.close);
  const rawMin = Math.min(...values), rawMax = Math.max(...values);
  const padding = Math.max((rawMax - rawMin) * .12, rawMax * .025);
  const min = Math.max(0, rawMin - padding), max = rawMax + padding;
  const x = index => left + index / (series.length - 1) * plotWidth;
  const y = value => top + (max - value) / Math.max(max - min, 1) * plotHeight;
  const points = series.map((point,index) => `${x(index).toFixed(2)},${y(point.close).toFixed(2)}`);
  const line = `M${points.join(' L')}`;
  const area = `${line} L${x(series.length - 1).toFixed(2)},${(top + plotHeight).toFixed(2)} L${left},${(top + plotHeight).toFixed(2)} Z`;
  const yTicks = Array.from({length:5}, (_,index) => min + (max - min) * index / 4).reverse();
  const xTicks = Array.from({length:9}, (_,index) => Math.round(index * (series.length - 1) / 8));
  const grids = yTicks.map(value => `<line class="grid-line" x1="${left}" y1="${y(value).toFixed(2)}" x2="${width-right}" y2="${y(value).toFixed(2)}"></line><text class="axis-label" x="${left-10}" y="${(y(value)+4).toFixed(2)}" text-anchor="end">${priceMoney(value)}</text>`).join('');
  const dates = xTicks.map(index => `<text class="axis-label" x="${x(index).toFixed(2)}" y="${height-14}" text-anchor="middle">${series[index].date.slice(0,7)}</text>`).join('');
  const markLayout = priceMarkSlots(series, txs || [], x, index => y(series[index].close), top, plotHeight);
  // 浮层读的事实表：索引就是 data-price-mark 的值。收盘价取该点序列值，和标记的 y 同源，
  // 不另算一遍，免得哪天两处算法漂开。
  // 刻度参数一并进 geo：悬浮游标要自己把「第 index 个日线点」换算成 x/y，用的是和曲线同一套公式，
  // 不另写一份，否则点会慢慢漂离曲线。
  priceChartGeo = {series, left, plotWidth, top, plotHeight, min, max, marks: markLayout.slots.map(slot => slot.folded
    ? {kind: slot.kind, folded: slot.folded.map(t => ({tx: t, close: series[nearestSeriesIndex(series, t.transaction_date)].close}))}
    : {kind: slot.kind, tx: slot.t, close: series[slot.index].close})};
  // 引线与头像分两趟画：先把**所有**引线铺完，再画头像。合并成一趟时，后画的那枚带着自己的
  // 引线一起压在先画的那枚上——卖块的引线要从下方一路连回曲线上自己的点，正好整条横穿买块的
  // 头像，红色虚线就印在人家脸上。分趟之后引线永远在全部头像之下，交叉只发生在线与线之间。
  const markStems = markLayout.slots.map(slot => {
    const at = `transform="translate(${slot.px.toFixed(2)},${slot.cy.toFixed(2)})"`;
    return `<line class="price-mark-stem ${slot.kind}" ${at} x1="0" y1="0" x2="0" y2="${(slot.py - slot.cy).toFixed(2)}"></line>`;
  }).join('');
  const markSvg = markStems + markLayout.slots.map(slot => {
    const at = `transform="translate(${slot.px.toFixed(2)},${slot.cy.toFixed(2)})"`;
    if (slot.folded) return `<g class="price-mark ${slot.kind}" data-price-mark="${slot.id}" ${at}><circle class="price-mark-more-ring" r="10"></circle><text class="price-mark-more-text" y="3.6" text-anchor="middle">+${slot.folded.length}</text></g>`;
    const portrait = PEOPLE[slot.t.person_id]?.portrait_data_uri || '';
    return `<g class="price-mark ${slot.kind}" data-price-mark="${slot.id}" ${at}><circle class="price-mark-ring" r="10"></circle><image class="price-mark-img" x="-9" y="-9" width="18" height="18" href="${portrait}" xlink:href="${portrait}" clip-path="url(#priceMarkClip-${market.ticker})"></image></g>`;
  }).join('');
  const markNote = markLayout.slots.length
    ? `；头像标出 ${markLayout.buyPeople} 位买入、${markLayout.sellPeople} 位卖出披露人的交易日位置，标记处为当日收盘价${markLayout.hidden ? `，另有 ${markLayout.hidden} 笔在 24 个月窗口外未标出` : ''}`
    : '';
  return `<svg class="stock-price-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${market.ticker} 近24个月模拟日线收盘价格走势${markNote}"><defs><linearGradient id="priceArea-${market.ticker}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#ffca3a" stop-opacity=".22"></stop><stop offset="100%" stop-color="#ffca3a" stop-opacity=".02"></stop></linearGradient><clipPath id="priceMarkClip-${market.ticker}"><circle r="9"></circle></clipPath></defs>${grids}<line class="axis-line" x1="${left}" y1="${top}" x2="${left}" y2="${top+plotHeight}"></line><line class="axis-line" x1="${left}" y1="${top+plotHeight}" x2="${width-right}" y2="${top+plotHeight}"></line>${dates}<path class="price-area" style="fill:url(#priceArea-${market.ticker})" d="${area}"></path><path class="price-line" d="${line}"></path><circle class="latest-dot" cx="${x(series.length-1).toFixed(2)}" cy="${y(series.at(-1).close).toFixed(2)}" r="5"></circle><rect class="price-hit" x="${left}" y="${top}" width="${plotWidth}" height="${plotHeight}"></rect>${markSvg}<g class="price-cursor"><line class="price-cursor-line" y1="${top}" y2="${top+plotHeight}"></line><circle class="price-cursor-dot" r="5"></circle></g></svg>`;
}

function renderPriority(txs) {
  const priority = DATA.people.filter(p => p.priority).sort((a, b) => {
    const aRank = Number.isInteger(a.demo_priority_rank) ? a.demo_priority_rank : Number.MAX_SAFE_INTEGER;
    const bRank = Number.isInteger(b.demo_priority_rank) ? b.demo_priority_rank : Number.MAX_SAFE_INTEGER;
    return aRank - bRank;
  });
  // short 是色块里的单字（买/卖），label/verb 是散文里用的全称——色块只有 20px 宽，
  // 「买入」两个字塞进去会把色块撑变形。色块本身 aria-hidden，方向语义由卡片 aria-label 承担。
  const flows = [
    {direction: 'purchase', short: '买', label: '买入', verb: '买入', side: 'buy'},
    {direction: 'sale', short: '卖', label: '卖出', verb: '卖出', side: 'sell'}
  ];
  document.querySelector('#priorityGrid').innerHTML = priority.map((person, index) => {
    const rank = Number.isInteger(person.demo_priority_rank) ? person.demo_priority_rank : index + 1;
    const role = hasDisplayValue(person.role) ? person.role : '';
    const group = IS_DEMO && hasDisplayValue(person.demo_priority_group) ? person.demo_priority_group : (role || '重点关注');
    const subtitle = [role, hasDisplayValue(person.party) ? partyLabel(person.party) : '', person.state].filter(hasDisplayValue).join(' · ');
    // 卡片是整块按钮，aria-label 会覆盖全部子内容：色块只显示单字「买/卖」且 aria-hidden，
    // 所以两个方向各自读了哪只标的、多少钱、哪天买、哪天披露，必须由 aria-label 完整说出来，
    // 否则屏幕阅读器用户拿到的就只有「查看 X 个人主页」这一句，卡片数据全部丢失。
    const spoken = [];
    const flowMarkup = flows.map(flow => {
      // 只统计带 ticker 的记录——没有代码的交易列不成 chip，若还计进「N 笔」，
      // 日期行说的笔数就会多于卡片上列出的标的所能解释的笔数。
      const rows = txs.filter(t => t.person_id === person.id && t.transaction_type === flow.direction && t.ticker);
      const securities = securitiesByAmount(rows, person.id, flow.direction);
      if (!securities.length) {
        spoken.push(`${flow.label}：${DASHBOARD_WINDOW} 天内无披露`);
        return `<span class="priority-flow ${flow.side} is-empty">
          <span class="flow-head"><span class="flow-tag" aria-hidden="true">${flow.short}</span></span>
          <span class="flow-empty">${DASHBOARD_WINDOW} 天内无${flow.verb}披露</span>
        </span>`;
      }
      // 每枚 chip 的浮层：该标的窗口内的合计区间；只有合计了多笔时才补笔数，
      // 单笔时那个「1 笔」是纯噪音，多笔时不标则会被读成单笔的区间。
      const tip = item => `${money(item.low)}–${money(item.high)}${item.count > 1 ? ` · ${item.count} 笔` : ''}`;
      const shown = securities.slice(0, FLOW_CHIP_LIMIT);
      const rest = securities.slice(FLOW_CHIP_LIMIT);
      // 被折起来的标的没有消失：+N 的浮层逐行列出余下每一只的代码与金额。
      // 截断只允许发生在「同时看到几枚」上，不允许让任何一笔金额变得不可达。
      // chip 是按钮（点了进单股页），所以它自己也要念得清：光标可悬停的那层浮层读屏拿不到，
      // 金额必须落在它的 aria-label 上——代码在前，与本板其它代码入口同一句式。
      const chips = shown.map(item => `<button class="flow-chip ${flow.side}" type="button" data-ticker-link="${item.ticker}" aria-label="查看 ${item.ticker} 股票页面：${tip(item)}">${item.ticker}<span class="chip-tip">${tip(item)}</span></button>`).join('')
        + (rest.length ? `<span class="flow-chip more">+${rest.length}<span class="chip-tip">${rest.map(item => `${item.ticker} ${money(item.low)}–${money(item.high)}`).join('<br>')}</span></span>` : '');
      // 日期行是方向级的：一个方向现在会列出多只标的，任何单只的日期都不再代表整行。
      // 「最新」两字不能省——省了就会被读成这几只都是那天买的。
      const agg = directionAggregate(rows, flow.direction);
      // 只数与笔数合成顶行色块右侧的那一句（段间仍由 CSS 补「·」）；
      // 交易日与披露日各自独占一行——竖排卡片只有半个卡片的宽度，串成一行会被挤到折行。
      const counts = [
        securities.length > 1 ? `${securities.length} 只` : '',
        agg.count > 1 ? `${agg.count} 笔` : ''
      ].filter(Boolean).map(part => `<span>${part}</span>`).join('');
      const dates = [
        `最新 ${dateLabel(agg.tradeDate)}${flow.verb}`,
        `${dateLabel(agg.filedAt)}披露`
      ].map(part => `<span>${part}</span>`).join('');
      // 卡片自己还是要口述全部内容：它整块可点（进人物页），读屏在浏览模式下先听到的就是这一句。
      // 所以这里必须把「实际渲染出来的每一只」都念出来，被截断时再补总数——
      // 只说总数会让人以为卡片列的就是全部，只说列出的又会丢失余量。
      const listed = shown.map(item => `${item.ticker} ${money(item.low)}–${money(item.high)}`).join('、');
      spoken.push(`${flow.label} ${listed}${rest.length ? ` 等 ${securities.length} 只` : ''}，共 ${agg.count} 笔，最新 ${dateLabel(agg.tradeDate)}${flow.verb}，${dateLabel(agg.filedAt)}披露`);
      // DOM 顺序即视觉顺序：顶行是色块 + 只数/笔数，然后是标的 chips，最后两行是方向级日期。
      return `<span class="priority-flow ${flow.side}">
        <span class="flow-head">
          <span class="flow-tag" aria-hidden="true">${flow.short}</span>
          ${counts ? `<span class="flow-counts">${counts}</span>` : ''}
        </span>
        <span class="flow-chips">${chips}</span>
        <span class="flow-meta">${dates}</span>
      </span>`;
    }).join('');
    // 根节点是 <article> 而不是 <button>：卡里现在有两类去处（姓名进人物页、每枚代码进单股页），
    // 整块可点只是鼠标上的便利，真正的键盘入口是姓名那个按钮——aria-label 也挂在它身上。
    // 点空白处进人物页由下面那条点击监听兜底；点到姓名或代码时，capture 阶段的委托监听会先
    // stopPropagation，事件根本到不了这条监听，所以不会一次点击跳两层。
    return `<article class="priority-card" data-person="${person.id}">
      <div class="priority-head">
        <img class="priority-avatar" src="${person.portrait_data_uri}" alt="${person.display_name}">
        <div class="priority-id">
          <button class="priority-who" type="button" data-person-link="${person.id}" aria-label="查看 ${person.display_name} 个人主页：${spoken.join('；')}">${person.display_name}</button>
          ${subtitle ? `<span class="priority-role">${subtitle}</span>` : ''}
        </div>
        <span class="priority-kicker">${String(rank).padStart(2, '0')} / ${group}</span>
      </div>
      <div class="priority-flows">${flowMarkup}</div>
    </article>`;
  }).join('');
  // 卡片空白处的兜底跳转。点到姓名或代码时事件已被 capture 阶段的委托监听拦下，这里不会跑。
  document.querySelectorAll('.priority-card').forEach(el => el.addEventListener('click', () => openPerson(el.dataset.person)));
}

// 两块各有一条 Tab 条，这里一次性把两条都刷成同一状态——所以点任意一条，两块一起切。
function renderDashboardView() {
  document.querySelectorAll('[data-window-view]').forEach(button => {
    const active = button.dataset.windowView === dashboardView;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll('[data-window-panel]').forEach(panel => {
    panel.hidden = panel.dataset.windowPanel !== dashboardView;
  });
}

function renderTimeline(days) {
  const txs = windowTransactions(days);
  const filters = timelineFilters[days];
  const query = filters.query.trim().toLowerCase();
  const filtered = txs.filter(t => {
    const p = PEOPLE[t.person_id];
    const matchesQuery = !query || [p.display_name, p.short_name, t.ticker, t.asset_name].some(value => String(value || '').toLowerCase().includes(query));
    const matchesDirection = filters.direction === 'all' || t.transaction_type === filters.direction;
    const matchesInstrument = filters.instrument === 'all' || t.instrument_type === filters.instrument;
    return matchesQuery && matchesDirection && matchesInstrument;
  });
  document.querySelector(`#timelineCount${days}`).textContent = `${days} DAYS / ${filtered.length} OF ${txs.length}`;
  document.querySelector(`#timeline${days}`).innerHTML = filtered.length ? filtered.map(t => {
    const p = PEOPLE[t.person_id];
    return `<article class="timeline-row" data-tx="${t.id}">
      <button class="avatar-person-link" data-person-link="${p.id}" aria-label="查看 ${p.display_name} 个人主页"><img class="avatar" src="${p.portrait_data_uri}" alt="${p.display_name}"></button>
      <button class="who text-person-link" data-person-link="${p.id}"><b>${p.display_name}</b><small>${t.owner === 'Self' ? '本人名下' : t.owner + ' 名下'} · ${dateLabel(t.filed_at)} 申报 · 滞后 ${t.disclosure_lag_days} 天</small></button>
      <button class="security ticker-link-button" data-ticker-link="${t.ticker}">${t.ticker}<small>${t.asset_name} · ${instrument(t)}</small></button>
      <div class="trade-block"><span class="trade-type ${txClass(t)}">${txLabel(t)}</span><div class="amount">${range(t)}</div></div>
      <div class="return ${returnClass(t.underlying_return_since_trade)}">${pct(t.underlying_return_since_trade)}<small>标的自交易日<br>${IS_DEMO ? '模拟涨跌' : 'EOD涨跌'}</small></div>
    </article>`;
  }).join('') : '<div class="timeline-empty">当前筛选条件下没有披露记录。</div>';
  document.querySelectorAll(`#timeline${days} .timeline-row`).forEach(el => el.addEventListener('click', () => openTransaction(el.dataset.tx)));
}

// 热门股票排序状态：每个窗口区块各一份，和「标的交易明细」的每块筛选同级——两块看的是不同窗口的数据，
// 一块按金额排、一块按涨跌排是合理诉求，联动反而会把其中一块强行改掉。
// 默认（关联人物降序）与改动前的固定排序链完全一致，所以首屏顺序不变。
const hotSort = {30: {key: 'people', direction: 'desc'}, 90: {key: 'people', direction: 'desc'}};
// 文本列默认升序（A→Z）：代码列若默认降序，首屏会排成 Z→A，没有读者要那个。
const HOT_SORT_TEXT_KEYS = new Set(['ticker']);

// 默认口径，兼作排序同值时的兜底，保证名次不会因为排序不稳定而跳动。
const hotDefaultOrder = (a, b) =>
  b.people.length - a.people.length ||
  b.related.length - a.related.length ||
  b.latest.localeCompare(a.latest) ||
  (a.market?.index ?? Number.MAX_SAFE_INTEGER) - (b.market?.index ?? Number.MAX_SAFE_INTEGER);

function hotSortValue(row, key) {
  if (key === 'ticker') return row.ticker;
  if (key === 'people') return row.people.length;
  if (key === 'amount') return row.high;
  return row.returnValue;
}

function sortHotRows(rows, key, direction) {
  const dir = direction === 'asc' ? 1 : -1;
  // 没有可比行情的行永远排在最后：涨跌显示为「—」时它既不是涨也不是跌，放在哪一端都会被读成一个结论。
  return rows.slice().sort((a, b) => {
    const av = hotSortValue(a, key), bv = hotSortValue(b, key);
    if (av === null || bv === null) {
      if ((av === null) !== (bv === null)) return av === null ? 1 : -1;
    } else if (key === 'ticker') {
      const cmp = String(av).localeCompare(String(bv));
      if (cmp) return cmp * dir;
    } else if (av !== bv) {
      return av < bv ? -dir : dir;
    }
    return hotDefaultOrder(a, b);
  });
}

function renderHotSortHead(days) {
  const state = hotSort[days];
  document.querySelectorAll(`#hotStockTable${days} .hs-sort`).forEach(button => {
    const active = button.dataset.hsSort === state.key;
    button.classList.toggle('active', active);
    button.classList.toggle('asc', active && state.direction === 'asc');
    button.classList.toggle('desc', active && state.direction === 'desc');
    button.setAttribute('aria-label', `按「${button.textContent}」排序${active ? `，当前${state.direction === 'asc' ? '升序' : '降序'}` : ''}`);
    const th = button.closest('th');
    if (th) th.setAttribute('aria-sort', active ? (state.direction === 'asc' ? 'ascending' : 'descending') : 'none');
  });
}

function bindHotSort() {
  // 绑在 <table> 上而不是按钮上：排序会整块替换 tbody，常驻的只有表头。
  // 同键点击翻转升降序，换键则重置为默认方向——与参考看板的排序按钮同一套手感。
  [30, 90].forEach(days => {
    const table = document.querySelector(`#hotStockTable${days}`);
    if (!table) return;
    table.addEventListener('click', event => {
      const button = event.target.closest('[data-hs-sort]');
      if (!button) return;
      const key = button.dataset.hsSort;
      const state = hotSort[days];
      if (state.key === key) state.direction = state.direction === 'desc' ? 'asc' : 'desc';
      else { state.key = key; state.direction = HOT_SORT_TEXT_KEYS.has(key) ? 'asc' : 'desc'; }
      renderHotStocks(days);
    });
  });
}

function renderHotStocks(days) {
  // 每个窗口区块各自取数：30 天块与 90 天块同时呈现，互不影响。
  const txs = windowTransactions(days);
  const marketRows = DATA.market_moves[String(days)] || [];
  document.querySelector(`#hotStockReturnScope${days}`).innerHTML = `<b>股价涨跌</b>本窗口首笔交易日至今的收盘价变化，不代表政客本人收益。`;
  const marketByTicker = new Map(marketRows.map((row, index) => [row.ticker, {...row, index}]));
  const directionGroups = {};
  txs.forEach(t => {
    if (!t.ticker) return;
    const key = `${t.ticker}|${t.transaction_type}`;
    if (!directionGroups[key]) directionGroups[key] = [];
    directionGroups[key].push(t);
  });
  const multiPersonTickers = Object.values(directionGroups)
    .filter(items => new Set(items.map(item => item.person_id)).size >= 2)
    .map(items => items[0].ticker);
  const tickers = [...new Set([...marketRows.map(row => row.ticker), ...multiPersonTickers])];
  const rows = tickers.map(ticker => {
    const related = txs.filter(t => t.ticker === ticker);
    const people = [...new Set(related.map(t => t.person_id))].filter(id => PEOPLE[id]);
    const stats = directionStats(related);
    const market = marketByTicker.get(ticker);
    const low = related.reduce((sum, t) => sum + Number(t.amount_low || 0), 0);
    const high = related.reduce((sum, t) => sum + Number(t.amount_high || 0), 0);
    const marketSnapshot = (DATA.security_market_data || []).find(item => item.ticker === ticker);
    const company = related[0]?.asset_name || marketSnapshot?.company || ticker;
    const latest = related.reduce((value, t) => t.filed_at > value ? t.filed_at : value, '');
    const hasReturn = market && market.return_since_first_trade !== null && market.return_since_first_trade !== undefined && Number.isFinite(Number(market.return_since_first_trade));
    const returnValue = hasReturn ? Number(market.return_since_first_trade) : null;
    const returnStart = market?.return_baseline_date || market?.first_transaction_date || related.map(t => t.transaction_date).sort()[0] || '—';
    const returnEnd = market?.price_as_of_date || marketSnapshot?.as_of_date || '—';
    return {ticker, related, people, stats, market, marketSnapshot, low, high, company, latest, hasReturn, returnValue, returnStart, returnEnd};
  }).filter(row => row.related.length);

  // 关注度名次先按默认口径定下来，之后无论用户怎么排序，「01」始终是关注人数最多的那只。
  // 若跟着当前排序重新编号，按涨跌降序时「01」就变成「涨幅第一」——那是这块看板不该做的排名声明。
  rows.slice().sort(hotDefaultOrder).forEach((row, index) => { row.attentionRank = index + 1; });

  const ordered = sortHotRows(rows, hotSort[days].key, hotSort[days].direction);
  renderHotSortHead(days);

  // 分方向明细行：多少人、多少笔、金额区间上下限分别求和、最新一笔的交易日与申报日。
  const flowRow = (verb, side, agg) => agg
    ? `<div class="hot-stock-flow-row ${side}"><span class="hs-side">${verb}</span><span class="hs-figures">${agg.people} 人 · ${agg.count} 笔</span><span class="hs-amount">${money(agg.low)}–${money(agg.high)}</span><span class="hs-dates">${dateLabel(agg.tradeDate)}${verb} · ${dateLabel(agg.filedAt)}披露</span></div>`
    : `<div class="hot-stock-flow-row ${side} is-empty"><span class="hs-side">${verb}</span><span class="hs-empty">本窗口无${verb}披露</span></div>`;

  document.querySelector(`#hotStocks${days}`).innerHTML = ordered.length ? ordered.map(row => {
    const {ticker, related, people, stats, market, low, high, company, hasReturn, returnValue, returnStart, returnEnd, attentionRank} = row;
    const buyPeople = new Set(stats.buys.map(t => t.person_id)).size;
    const sellPeople = new Set(stats.sells.map(t => t.person_id)).size;
    const signal = stats.buys.length && stats.sells.length ? '买卖分歧' : stats.buys.length ? `${buyPeople > 1 ? '多人' : ''}买入` : stats.sells.length ? `${sellPeople > 1 ? '多人' : ''}卖出` : '披露关注';
    const signalClass = stats.buys.length && stats.sells.length ? 'mixed' : stats.buys.length ? 'buy' : stats.sells.length ? 'sell' : '';
    const segments = `${stats.buys.length ? `<button class="bar-segment buy" data-ticker="${ticker}" data-side="purchase" style="flex:${stats.buys.length}" aria-label="查看 ${ticker} 的 ${stats.buys.length} 条买入披露"></button>` : ''}${stats.sells.length ? `<button class="bar-segment sell" data-ticker="${ticker}" data-side="sale" style="flex:${stats.sells.length}" aria-label="查看 ${ticker} 的 ${stats.sells.length} 条卖出披露"></button>` : ''}${stats.others.length ? `<button class="bar-segment other" data-ticker="${ticker}" data-side="other" style="flex:${stats.others.length}" aria-label="查看 ${ticker} 的其他披露"></button>` : ''}`;
    const visiblePeople = people.slice(0, 6);
    const avatars = visiblePeople.map(id => `<button class="avatar-person-link" data-person-link="${id}" title="查看 ${PEOPLE[id].display_name} 个人主页" aria-label="查看 ${PEOPLE[id].display_name} 个人主页"><img src="${PEOPLE[id].portrait_data_uri}" alt="${PEOPLE[id].display_name}"></button>`).join('');
    const overflow = people.length > visiblePeople.length ? `<span class="hot-stock-more">+${people.length - visiblePeople.length}</span>` : '';
    return `<tr class="hot-stock-row">
      <td class="hs-cell-identity"><button class="hot-stock-identity" data-ticker-link="${ticker}" aria-label="查看 ${ticker} 股票页面"><span class="hot-stock-rank">${String(attentionRank).padStart(2, '0')}</span><span><b class="hot-stock-ticker">${ticker}</b><small class="hot-stock-company">${company}</small></span></button></td>
      <td class="hs-cell-people"><div class="hot-stock-people"><div class="hot-stock-attention"><b>${people.length} 位人物</b><span>${related.length} 笔披露</span><span class="hot-stock-signal ${signalClass}">${signal}</span></div><div class="hot-stock-avatar-stack">${avatars}${overflow}</div></div></td>
      <td class="hs-cell-flow"><div class="hot-stock-flow">
        <div class="hot-stock-flow-rows">${flowRow('买入', 'buy', directionAggregate(related, 'purchase'))}${flowRow('卖出', 'sell', directionAggregate(related, 'sale'))}</div>
        <div class="split-bar">${segments}</div>
        <span class="hot-stock-amount">合计 ${related.length} 笔 · 披露区间 ${money(low)}–${money(high)}</span>
      </div></td>
      <td class="hs-cell-return"><button class="hot-stock-return ${hasReturn ? returnClass(returnValue) : ''}" data-ticker-link="${ticker}" aria-label="查看 ${ticker} 股票页面">${hasReturn ? pct(returnValue) : '—'}<small>${hasReturn ? `${returnStart}<br>→ ${returnEnd} EOD` : '暂无可比窗口行情'}</small></button></td>
    </tr>`;
  }).join('') : '<tr><td class="hot-stock-empty" colspan="4">当前窗口没有可展示的热门关注股票。</td></tr>';
  document.querySelectorAll(`#hotStocks${days} .bar-segment`).forEach(el => el.addEventListener('click', () => openTicker(el.dataset.ticker, el.dataset.side)));
}

function renderSources() {
  const allowed = new Set(DATA.processing?.disclosure_source_ids || []);
  const latest = new Map();
  DATA.source_health.filter(s => allowed.has(s.source_id) && s.source_url).forEach(s => {
    const current = latest.get(s.source_id);
    const stamp = s.last_successful_sync_at || s.data_cutoff_at || s.last_checked_at || '';
    const currentStamp = current && (current.last_successful_sync_at || current.data_cutoff_at || current.last_checked_at || '');
    if (!current || stamp > currentStamp) latest.set(s.source_id, s);
  });
  const rows = [...latest.values()].sort((a,b) => String(b.last_successful_sync_at || b.data_cutoff_at || b.last_checked_at || '').localeCompare(String(a.last_successful_sync_at || a.data_cutoff_at || a.last_checked_at || '')));
  document.querySelector('#sourceLinks').innerHTML = rows.length ? rows.map(s => {
    const name = String(s.source || s.source_id).replace(/\s*·\s*模拟.*$/, '');
    const checked = s.last_successful_sync_at || s.data_cutoff_at || s.last_checked_at;
    const status = s.status === 'ok' ? '已同步' : '延迟';
    return `<a class="source-link" href="${s.source_url}" target="_blank" rel="noreferrer"><span class="source-link-copy"><b><i class="source-dot ${s.status}"></i>${name}</b><small>${status} · ${dateLabel(checked)}</small></span><span class="source-link-action">官方来源 ↗</span></a>`;
  }).join('') : '<div class="source-empty">当前快照没有可用的官方数据源链接。</div>';
}

function openDrawer(html) {
  document.querySelector('#drawerContent').innerHTML = html;
  document.querySelector('#drawer').classList.add('open');
  document.querySelector('#drawerBackdrop').classList.add('open');
  document.querySelector('#drawer').setAttribute('aria-hidden', 'false');
}

function drawerShell(p, inner) {
  return `<div class="drawer-hero"><button class="drawer-person-photo person-link-button" data-person-link="${p.id}" aria-label="查看 ${p.display_name} 个人主页"><img src="${p.portrait_data_uri}" alt="${p.display_name}"></button></div><div class="drawer-body"><h2><button class="drawer-person-name person-link-button" data-person-link="${p.id}">${p.display_name}</button></h2><div class="drawer-role">${p.role} · ${p.party} · ${p.state} · 关联披露，不等同本人交易</div>${inner}</div>`;
}

function openTransaction(id) {
  const t = DATA.transactions.find(item => item.id === id), p = PEOPLE[t.person_id];
  const optionFacts = t.instrument_type === 'Option' ? `<div class="fact"><span>期权类型</span><b>${t.option_type}</b></div><div class="fact"><span>行权价 / 到期日</span><b>$${t.strike_price} / ${t.expiration_date}</b></div>` : '';
  const evidence = IS_DEMO
    ? '<h3>无原始申报链接</h3>'
    : `<h3>${t.verification_status === 'official_matched' ? '已匹配官方申报' : '记录状态待核验'}</h3><p>${t.source || t.source_id}${t.source_url ? ` · <a href="${t.source_url}" target="_blank" rel="noreferrer">查看官方原文 ↗</a>` : ''}</p>`;
  openDrawer(drawerShell(p, `<div class="drawer-callout"><b>${IS_DEMO ? 'SIMULATED DISCLOSURE' : 'OFFICIAL DISCLOSURE'}</b><h3>${txLabel(t)} <button class="ticker-link-button" data-ticker-link="${t.ticker || ''}">${t.ticker || '未映射代码'}</button></h3><p>${t.asset_name} · ${t.owner === 'Self' ? '本人名下' : t.owner + ' 名下'}。${IS_DEMO ? '这是无事实效力的界面模拟记录。' : '这是公开申报记录，不等同于当前仓位。'}</p></div><div class="fact-grid"><div class="fact"><span>披露金额区间</span><b>${range(t)}</b></div><div class="fact"><span>交易类型</span><b>${instrument(t)}</b></div><div class="fact"><span>交易日</span><b>${t.transaction_date}</b></div><div class="fact"><span>申报时间</span><b>${t.filed_at.replace('T',' ')}</b></div><div class="fact"><span>披露滞后</span><b>${t.disclosure_lag_days} 天</b></div><div class="fact"><span>标的自交易日</span><b class="${returnClass(t.underlying_return_since_trade)}">${pct(t.underlying_return_since_trade)}${IS_DEMO ? ' · 模拟' : ''}</b></div>${optionFacts}</div><div class="drawer-callout"><b>证据状态</b>${evidence}</div>`));
}

function openPerson(id) {
  activePersonId = id;
  personDirection = 'all';
  closeDrawer();
  renderPersonPage();
  const personPage = document.querySelector('#personPage');
  const stockPage = document.querySelector('#stockPage');
  document.querySelector('#personBack').textContent = stockPage.classList.contains('open') && activeStockTicker ? `← 返回 ${activeStockTicker} 单股页` : '← 返回全局看板';
  personPage.classList.add('open');
  personPage.setAttribute('aria-hidden', 'false');
  document.body.classList.add('person-view-open');
  personPage.scrollTop = 0;
}

function closePersonPage() {
  document.querySelector('#personPage').classList.remove('open');
  document.querySelector('#personPage').setAttribute('aria-hidden', 'true');
  document.body.classList.remove('person-view-open');
}

function openTickerFromPerson(ticker) {
  const personId = activePersonId;
  closePersonPage();
  openTicker(ticker, 'all', personId);
}

function renderPersonPage() {
  if (!activePersonId) return;
  const p = PEOPLE[activePersonId];
  const reference = IS_DEMO && p.demo_reference_snapshot ? p.demo_reference_snapshot : null;
  const txs = windowTransactions(activeWindow).filter(t => t.person_id === activePersonId);
  const allPersonTxs = DATA.transactions.filter(t => t.person_id === activePersonId).sort((a,b) => new Date(b.filed_at) - new Date(a.filed_at));
  const latest = txs[0] || allPersonTxs[0];
  const s = windowSummary(txs);
  const direction = directionStats(txs);
  const byTicker = {};
  txs.forEach(t => {
    if (!byTicker[t.ticker]) byTicker[t.ticker] = [];
    byTicker[t.ticker].push(t);
  });
  const topTickers = Object.entries(byTicker).sort((a,b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  const referenceTopTickers = reference?.top_tickers || [];
  const referenceTotalValue = reference?.estimated_total_display || (reference ? compactMoney(reference.estimated_total_usd) : '—');
  const topTickerNames = (reference ? referenceTopTickers.map(item => item.ticker) : topTickers.map(([ticker]) => ticker)).slice(0,3).join('、') || '暂无明确股票代码';
  const summary = reference
    ? `${p.display_name}（${p.role} · ${partyLabel(p.party)}）模拟参考汇总 ${yuan.format(reference.disclosed_transactions)} 笔证券交易（${yuan.format(reference.buy_count)} 买 / ${yuan.format(reference.sell_count)} 卖），累计估值约 ${referenceTotalValue}，最常涉及 ${topTickerNames}。披露延迟中位数 ${reference.median_disclosure_lag_days} 天，最新披露 ${reference.latest_filing_date}。`
    : `${p.display_name}（${p.role} · ${partyLabel(p.party)} · ${p.state}）在最近 ${activeWindow} 天内有 ${txs.length} 笔关联证券交易披露（${direction.buys.length} 买 / ${direction.sells.length} 卖），披露金额上下限分别合计 ${compactMoney(s.low)}–${compactMoney(s.high)}，最常涉及 ${topTickerNames}。最新一笔于 ${latest ? latest.filed_at.slice(0,10) : '—'} 申报。`;

  const countBy = key => txs.reduce((acc,t) => { const label = key(t); acc[label] = (acc[label] || 0) + 1; return acc; }, {});
  const breakdown = counts => Object.entries(counts).sort((a,b) => b[1]-a[1]).map(([label,count]) => `<div class="person-breakdown-row"><div class="person-breakdown-copy"><span>${label}</span><b>${count} 条</b></div><div class="person-breakdown-track"><i style="width:${txs.length ? count / txs.length * 100 : 0}%"></i></div></div>`).join('');
  const ownerRows = breakdown(countBy(t => ownerLabel(t.owner)));
  const instrumentRows = breakdown(countBy(t => t.instrument_type === 'Option' ? `${t.option_type} 期权` : t.instrument_type));

  // 「交易日至今涨跌幅」= 交易日起到行情截止日的标的价格变化（underlying_return_since_trade），
  // 由 process_snapshot 用拆股复权 SIP 收盘价算好，这里只负责展示。
  // 它与「申报日 / 滞后」是两件不相干的事：一端算交易日，一端算申报日，不能合并成净口径。
  // 没有行情序列时该字段为 null → returnClass 不给类、pct 给「—」，缺值不着色。
  const visibleTxs = txs.filter(t => personDirection === 'all' || t.transaction_type === personDirection);
  const tradeRows = visibleTxs.map(t => `<tr data-person-tx="${t.id}"><td><button class="person-security ticker-link-button" data-ticker-link="${t.ticker}"><b>${t.ticker}</b><small>${t.asset_name}</small></button></td><td>${ownerLabel(t.owner)}</td><td><span class="trade-type ${txClass(t)}">${txLabel(t)}</span></td><td><strong>${range(t)}</strong></td><td>${t.transaction_date}</td><td class="table-return"><span class="${returnClass(t.underlying_return_since_trade)}">${pct(t.underlying_return_since_trade)}</span></td><td>${t.filed_at.slice(0,10)}</td><td>${t.disclosure_lag_days} 天</td><td>${instrument(t)}</td></tr>`).join('');
  const oneYearTxs = txs.filter(t => Number.isFinite(t.underlying_return_1y_after_filing));
  const eligibleSinceFiling = txs.filter(t => Number.isFinite(t.underlying_return_since_filing));
  const performanceSample = oneYearTxs.length ? oneYearTxs : eligibleSinceFiling;
  const performanceValue = t => oneYearTxs.length ? Number(t.underlying_return_1y_after_filing) : Number(t.underlying_return_since_filing);
  const directionalTxs = performanceSample.filter(t => ['purchase','sale'].includes(t.transaction_type));
  const aligned = directionalTxs.filter(t => t.transaction_type === 'purchase' ? performanceValue(t) >= 0 : performanceValue(t) <= 0).length;
  const alignmentRate = directionalTxs.length ? aligned / directionalTxs.length * 100 : 0;
  const performanceValues = performanceSample.map(performanceValue).sort((a,b) => a-b);
  const medianPerformance = performanceValues.length ? performanceValues[Math.floor(performanceValues.length / 2)] : 0;
  // 人物页不再分页签。原来的「画像」页签去掉集合数据、买卖结构、申报持仓、年度披露量、
  // 最常交易股票之后，只剩一段总述——那点内容摘要里已经逐字说过了，所以整块不再单独成卡。
  // 页面主体就是 .person-main 一列：披露后表现观察（上）+ 交易明细披露表（下）。
  // 表现观察只有三个聚合数字，比明细表短得多，先给结论再给证据。
  // 原来的「大额 / 期权披露明细」卡片墙已删除：它按「大额优先」重排 performanceSample，
  // 与下方表格同源，逐笔唯一多出来的字段是「披露至今该股涨跌」——而这只在整块样本上取中位数
  // 时才成立，逐笔读数会被当成该人的单笔收益。删掉后 underlying_return_since_filing 仍由
  // 上面的表现观察聚合消费，没有变成死字段。
  const performanceBody = reference
    ? `<section class="performance-observation"><header class="performance-observation-head"><h3>披露后表现观察</h3></header><div class="performance-observation-grid"><div class="performance-observation-stat"><span>有1年数据样本</span><b>${yuan.format(reference.performance_sample_1y)} 笔</b></div><div class="performance-observation-stat"><span>方向吻合率</span><b>${reference.direction_match_pct}%</b></div><div class="performance-observation-stat"><span>披露后1年中位涨跌</span><b class="${returnClass(reference.median_return_1y_pct)}">${pct(reference.median_return_1y_pct)}</b></div></div><p class="performance-observation-note">方向吻合率＝买入后上涨、卖出后下跌的比例。只反映标的价格变化，不是本人盈亏。</p></section>`
    : `<section class="performance-observation"><header class="performance-observation-head"><h3>披露后表现观察</h3></header><div class="performance-observation-grid"><div class="performance-observation-stat"><span>${oneYearTxs.length ? '有1年数据样本' : '当前窗口有效样本'}</span><b>${performanceSample.length} 笔</b></div><div class="performance-observation-stat"><span>方向吻合率</span><b>${alignmentRate.toFixed(0)}%</b></div><div class="performance-observation-stat"><span>${oneYearTxs.length ? '披露后1年中位涨跌' : '披露至今中位涨跌'}</span><b class="${returnClass(medianPerformance)}">${pct(medianPerformance)}</b></div></div><p class="performance-observation-note">方向吻合率＝买入后上涨、卖出后下跌的比例。只反映标的价格变化，不是本人盈亏；标的可能已平仓或对冲。</p></section>`;
  const tradesBody = `<section class="person-layout"><main class="person-main">${performanceBody}<section class="person-card"><header class="person-card-head"><div><h3>交易明细披露</h3><p>${visibleTxs.length} 条记录 · 涨跌为标的价格变化，不代表本人收益</p></div><div class="stock-filter"><button data-person-direction="all" class="${personDirection === 'all' ? 'active' : ''}">全部</button><button data-person-direction="purchase" class="${personDirection === 'purchase' ? 'active' : ''}">买入</button><button data-person-direction="sale" class="${personDirection === 'sale' ? 'active' : ''}">卖出</button></div></header><div class="person-table-wrap">${tradeRows ? `<table class="person-table"><thead><tr><th>标的</th><th>所有人</th><th>方向</th><th>金额</th><th>交易日</th><th>交易日至今涨跌幅</th><th>申报日</th><th>滞后</th><th>交易类型</th></tr></thead><tbody>${tradeRows}</tbody></table>` : '<div class="person-empty">当前方向没有披露记录。</div>'}</div></section></main><aside class="person-aside"><section class="person-card"><header class="person-card-head"><div><h3>所有人归属</h3><p>${reference ? '模拟参考明细' : '本人、配偶与共同账户分开'}</p></div></header><div class="person-breakdown">${ownerRows || '<div class="person-empty">暂无记录</div>'}</div></section><section class="person-card"><header class="person-card-head"><div><h3>交易类型</h3><p>${reference ? '模拟参考明细' : '股票与期权分别列出'}</p></div></header><div class="person-breakdown">${instrumentRows || '<div class="person-empty">暂无记录</div>'}</div></section><section class="person-card"><div class="person-note"><b>SOURCE</b>数据来自 OGE / House Clerk / Senate eFD 官方申报${IS_DEMO ? '；模拟数据不提供原文链接。' : '，点击明细可查看原文。'}</div></section></aside></section>`;
  document.querySelectorAll('[data-person-window]').forEach(b => b.classList.toggle('active', Number(b.dataset.personWindow) === activeWindow));
  const identityLink = p.portrait_source_url ? `<a href="${p.portrait_source_url}" target="_blank" rel="noreferrer">官方身份页面 ↗</a>` : '<span>身份页面未提供</span>';
  const referenceLink = reference ? `<a href="${reference.reference_url}" target="_blank" rel="noreferrer">模拟汇总参考 · ${reference.reference_source} · ${reference.reference_captured_at} ↗</a>` : '';
  const scopeLabel = reference ? 'HISTORICAL DEMO SNAPSHOT' : `${activeWindow} DAY WINDOW`;
  document.querySelector('#personPageContent').innerHTML = `<section class="person-hero"><div class="person-hero-grid"><div class="person-portrait"><img src="${p.portrait_data_uri}" alt="${p.display_name}"><span class="person-photo-label">${p.portrait_is_placeholder ? 'IDENTITY PLACEHOLDER' : 'OFFICIAL IDENTITY IMAGE'}</span></div><div class="person-identity"><div class="person-eyebrow">${p.role} · ${partyLabel(p.party)}</div><h1 class="person-name">${p.display_name}</h1><div class="person-roleline"><span>${p.state}</span><span>${scopeLabel}</span><span>关联披露，不等同本人交易</span></div><div class="person-summary"><b>摘要</b><br>${summary}</div><div class="person-source">${identityLink}${referenceLink}<span>${IS_DEMO ? '原始交易申报 · 模拟数据不提供链接' : '原始交易申报 · 见各交易明细'}</span><span>数据截点 ${DATA.meta.data_cutoff_at.replace('T',' ')}</span></div></div></div></section>${tradesBody}`;

  document.querySelectorAll('[data-person-direction]').forEach(button => button.addEventListener('click', () => { personDirection = button.dataset.personDirection; renderPersonPage(); }));
  document.querySelectorAll('[data-person-tx]').forEach(row => row.addEventListener('click', () => openTransaction(row.dataset.personTx)));
}

function openTicker(ticker, focus = 'all', personId = null) {
  returnToPersonId = personId;
  activeStockTicker = ticker;
  stockDirection = ['purchase', 'sale'].includes(focus) ? focus : 'all';
  closeDrawer();
  renderStockPage();
  document.querySelector('#stockBack').textContent = returnToPersonId ? `← 返回 ${PEOPLE[returnToPersonId].display_name}` : '← 返回全局看板';
  document.querySelector('#stockPage').classList.add('open');
  document.querySelector('#stockPage').setAttribute('aria-hidden', 'false');
  document.body.classList.add('stock-view-open');
  document.querySelector('#stockPage').scrollTop = 0;
}

function closeStockPage() {
  document.querySelector('#stockPage').classList.remove('open');
  document.querySelector('#stockPage').setAttribute('aria-hidden', 'true');
  document.body.classList.remove('stock-view-open');
  activeStockTicker = null;
}

function handleStockBack() {
  const personId = returnToPersonId;
  returnToPersonId = null;
  closeStockPage();
  if (personId) openPerson(personId);
  else render();
}

function renderStockPage() {
  if (!activeStockTicker) return;
  const ticker = activeStockTicker;
  const allTickerTxs = DATA.transactions.filter(t => t.ticker === ticker).sort((a,b) => new Date(b.filed_at) - new Date(a.filed_at));
  const txs = windowTransactions(activeWindow).filter(t => t.ticker === ticker);
  const market = (DATA.security_market_data || []).find(item => item.ticker === ticker);
  const referenceEntries = [];
  if (IS_DEMO) DATA.people.forEach(person => {
    const reference = person.demo_reference_snapshot;
    if (!reference) return;
    const aggregate = (reference.top_tickers || []).find(item => item.ticker === ticker);
    if (aggregate) {
      referenceEntries.push({kind:'aggregate', person, reference, company:aggregate.company, buy_count:aggregate.buy_count, sell_count:aggregate.sell_count, transaction_count:aggregate.transaction_count});
      return;
    }
    (reference.notable_transactions || []).filter(item => item.ticker === ticker).forEach(item => referenceEntries.push({kind:'detail', person, reference, company:item.company, buy_count:item.transaction_type === 'purchase' ? 1 : 0, sell_count:item.transaction_type === 'sale' ? 1 : 0, transaction_count:1, detail:item}));
  });
  const referenceOnly = !txs.length && referenceEntries.length > 0;
  const referenceBuyCount = referenceEntries.reduce((sum,item) => sum + item.buy_count, 0);
  const referenceSellCount = referenceEntries.reduce((sum,item) => sum + item.sell_count, 0);
  const referenceTransactionCount = referenceEntries.reduce((sum,item) => sum + item.transaction_count, 0);
  const referencePeopleCount = new Set(referenceEntries.map(item => item.person.id)).size;
  const company = market?.company || allTickerTxs[0]?.asset_name || DATA.reported_holdings.find(h => h.ticker === ticker)?.asset_name || referenceEntries[0]?.company || ticker;
  const s = windowSummary(txs);
  const direction = directionStats(txs);
  const peopleCount = new Set(txs.map(t => t.person_id)).size;
  const latest = txs[0];
  const displayBuyCount = referenceOnly ? referenceBuyCount : direction.buys.length;
  const displaySellCount = referenceOnly ? referenceSellCount : direction.sells.length;
  const displayTransactionCount = referenceOnly ? referenceTransactionCount : txs.length;
  const displayPeopleCount = referenceOnly ? referencePeopleCount : peopleCount;
  const signal = displayBuyCount && displaySellCount ? '买卖方向存在分歧' : displayBuyCount ? '买入披露占主导' : displaySellCount ? '卖出披露占主导' : '暂无方向性记录';
  const attention = displayTransactionCount >= 3 ? '高关注' : displayTransactionCount >= 2 ? '有活动' : '低频';
  const quarter = quarterInfo(DATA.meta.data_cutoff_at);
  const quarterTxs = allTickerTxs.filter(t => t.transaction_date >= quarter.start && t.transaction_date <= quarter.end);
  const allowedEffectBases = ['filing_explicit','matched_holding_comparison','simulated'];
  const classifiedQuarterTxs = quarterTxs.filter(t => allowedEffectBases.includes(t.position_effect_basis) && ['new_position','increase','reduce','close'].includes(t.position_effect));
  const effectPeople = effects => new Set(classifiedQuarterTxs.filter(t => effects.includes(t.position_effect)).map(t => t.person_id)).size;
  const newPositionPeople = effectPeople(['new_position']);
  const increasePeople = effectPeople(['increase']);
  const reducePeople = effectPeople(['reduce','close']);
  const unclassifiedEffects = quarterTxs.length - classifiedQuarterTxs.length;
  const quarterDelta = market && Number(market.previous_quarter_end_price) ? (Number(market.current_price) / Number(market.previous_quarter_end_price) - 1) * 100 : NaN;
  const trend = Number.isFinite(quarterDelta) ? quarterDelta > 1 ? '升温' : quarterDelta < -1 ? '降温' : '平稳' : '待补行情';
  const referenceDetailEntries = referenceEntries.filter(item => item.kind === 'detail');
  const referenceAmountRange = referenceDetailEntries.length
    ? `${compactMoney(referenceDetailEntries.reduce((sum,item) => sum + item.detail.amount_low, 0))}–${compactMoney(referenceDetailEntries.reduce((sum,item) => sum + item.detail.amount_high, 0))}`
    : '—';
  const amountRange = referenceOnly ? referenceAmountRange : txs.length ? `${compactMoney(s.low)}–${compactMoney(s.high)}` : '—';
  const priceSummary = market ? `当前${IS_DEMO ? '模拟 ' : ''}EOD 价格 ${priceMoney(market.current_price)}，较上季末 ${pricePct(quarterDelta)}` : '当前快照未提供行情数据';
  const referenceNames = referenceEntries.map(item => item.person.display_name).filter((name,index,all) => all.indexOf(name) === index).join('、');
  const referenceCapture = referenceEntries[0]?.reference.reference_captured_at || '—';
  const referenceLatest = referenceEntries.map(item => item.reference.latest_filing_date).sort().at(-1) || '—';
  const referenceSuffix = referenceEntries.length ? `另有 ${referenceNames} 的模拟参考：${referenceTransactionCount} 次（${referenceBuyCount} 买 / ${referenceSellCount} 卖），截取 ${referenceCapture}；不计入本窗口。` : '';
  const summary = referenceOnly
    ? `${company}（${ticker}）在模拟参考中关联 ${referenceNames}，共 ${referenceTransactionCount} 次披露（${referenceBuyCount} 买 / ${referenceSellCount} 卖），最新披露 ${referenceLatest}。${referenceAmountRange === '—' ? '' : `可见明细金额区间 ${referenceAmountRange}；`}${priceSummary}。参考不代表当前仓位。`
    : `${company}（${ticker}）在最近 ${activeWindow} 天内有 ${txs.length} 笔政客及官员 STOCK Act / OGE 关联交易披露，涉及 ${peopleCount} 位人物，其中 ${direction.buys.length} 笔买入、${direction.sells.length} 笔卖出。披露金额上下限分别合计 ${amountRange}，最新一笔于 ${latest ? latest.filed_at.slice(0,10) : '—'} 申报；${priceSummary}。${referenceSuffix}仅统计政客披露，不含机构持仓与内部人交易。`;
  const segments = `${displayBuyCount ? `<button class="bar-segment buy" data-stock-focus="purchase" style="flex:${displayBuyCount}" aria-label="只看 ${displayBuyCount} 条买入披露"></button>` : ''}${displaySellCount ? `<button class="bar-segment sell" data-stock-focus="sale" style="flex:${displaySellCount}" aria-label="只看 ${displaySellCount} 条卖出披露"></button>` : ''}${!referenceOnly && direction.others.length ? `<span class="bar-segment other" style="flex:${direction.others.length}"></span>` : ''}`;
  const visibleTxs = txs.filter(t => stockDirection === 'all' || t.transaction_type === stockDirection);
  const visibleReferenceEntries = referenceEntries.filter(item => stockDirection === 'all' || (stockDirection === 'purchase' ? item.buy_count > 0 : item.sell_count > 0));
  const visibleReferenceCount = visibleReferenceEntries.reduce((sum,item) => sum + (stockDirection === 'purchase' ? item.buy_count : stockDirection === 'sale' ? item.sell_count : item.transaction_count), 0);
  const effectLabel = effect => ({new_position:'新建仓', increase:'加仓', reduce:'减仓', close:'清仓'}[effect] || '未分类');
  const tableRows = visibleTxs.map(t => {
    const p = PEOPLE[t.person_id];
    return `<tr data-stock-tx="${t.id}"><td><button class="stock-person" data-person-link="${p.id}"><img src="${p.portrait_data_uri}" alt="${p.display_name}"><span><b>${p.display_name}</b><small>${p.role} · ${ownerLabel(t.owner)}</small></span></button></td><td><div class="sd-direction"><span class="trade-type ${txClass(t)}">${t.transaction_type === 'purchase' ? '买' : t.transaction_type === 'sale' ? '卖' : '其他'}</span><small>${effectLabel(t.position_effect)} · ${instrument(t)}</small></div></td><td class="sd-amount">${range(t)}</td><td class="sd-date">${t.transaction_date}</td><td class="sd-date">${t.filed_at.slice(0,10)}<br><small>延迟 ${t.disclosure_lag_days} 天</small></td></tr>`;
  }).join('');
  const referenceTableRows = visibleReferenceEntries.map(item => {
    const p = item.person;
    if (item.kind === 'aggregate') return `<tr><td><button class="stock-person" data-person-link="${p.id}"><img src="${p.portrait_data_uri}" alt="${p.display_name}"><span><b>${p.display_name}</b><small>${p.role} · 所有人未提供 · 模拟参考</small></span></button></td><td><div class="sd-direction"><span class="sd-pills"><span class="trade-type purchase">${item.buy_count} 买</span><span class="trade-type sale">${item.sell_count} 卖</span></span><small>模拟参考 · 未拆分交易类型</small></div></td><td class="sd-amount">—<br><small>参考未提供</small></td><td class="sd-date">—<br><small>历史聚合</small></td><td class="sd-date">${item.reference.latest_filing_date}<br><small>参考页最新披露</small></td></tr>`;
    const t = item.detail;
    return `<tr><td><button class="stock-person" data-person-link="${p.id}"><img src="${p.portrait_data_uri}" alt="${p.display_name}"><span><b>${p.display_name}</b><small>${p.role} · 所有人未提供 · 模拟参考</small></span></button></td><td><div class="sd-direction"><span class="trade-type ${txClass(t)}">${t.transaction_type === 'purchase' ? '买' : '卖'}</span><small>持仓作用未提供 · ${t.instrument_type}</small></div></td><td class="sd-amount">${range(t)}</td><td class="sd-date">${t.transaction_date}</td><td class="sd-date">${t.filed_date}<br><small>延迟 ${t.lag_days} 天</small></td></tr>`;
  }).join('');

  const buyPeople = referenceOnly ? new Set(referenceEntries.filter(item => item.buy_count).map(item => item.person.id)) : new Set(direction.buys.map(t => t.person_id));
  const sellPeople = referenceOnly ? new Set(referenceEntries.filter(item => item.sell_count).map(item => item.person.id)) : new Set(direction.sells.map(t => t.person_id));
  // 「关联政客 / 官员」这格改为人物头像堆叠，每枚都能点进人物页。按该人在本窗口的披露笔数
  // 降序排、并列按姓名升序；最多铺 5 枚，其余收进 +N（余下姓名在 title 里逐个列出，不能只剩个数）。
  // 头像用的是 capture 阶段那支全局 data-person-link 委托：renderStockPage 每次都重建整块
  // innerHTML，自己绑的监听会被重渲抹掉。原来那格「政客交易 N 笔」已删——下方「政客交易明细」
  // 卡头已经写着当前窗口笔数，两处重复。
  const peopleTally = new Map();
  (referenceOnly
    ? referenceEntries.map(item => ({id: item.person.id, n: item.transaction_count}))
    : txs.map(t => ({id: t.person_id, n: 1}))
  ).forEach(item => peopleTally.set(item.id, (peopleTally.get(item.id) || 0) + item.n));
  const peopleIds = [...peopleTally.entries()]
    .sort((a,b) => b[1] - a[1] || PEOPLE[a[0]].display_name.localeCompare(PEOPLE[b[0]].display_name))
    .map(entry => entry[0]);
  const shownPeopleIds = peopleIds.slice(0, STOCK_AVATAR_LIMIT);
  const restPeopleIds = peopleIds.slice(STOCK_AVATAR_LIMIT);
  const peopleAvatars = `${shownPeopleIds.map(id => `<button class="avatar-person-link" type="button" data-person-link="${id}" aria-label="查看 ${PEOPLE[id].display_name} 个人主页"><img src="${PEOPLE[id].portrait_data_uri}" alt="${PEOPLE[id].display_name}"></button>`).join('')}${restPeopleIds.length ? `<span class="stock-metric-more" title="${restPeopleIds.map(id => PEOPLE[id].display_name).join('、')}">+${restPeopleIds.length}</span>` : ''}`;
  const metrics = [
    ['关联政客 / 官员', `${displayPeopleCount}位`, `<div class="stock-metric-people">${peopleAvatars || '<span class="stock-metric-more">—</span>'}</div>`, ''],
    ['现价', market ? priceMoney(market.current_price) : '—', market ? `<small>较上季末</small><b class="stock-metric-change ${returnClass(quarterDelta)}">${pricePct(quarterDelta)}</b>` : '<small>等待行情数据</small>', 'stock-metric-price'],
    ['披露金额区间', amountRange, '', '']
  ];

  const displayDirectionTotal = Math.max(displayBuyCount + displaySellCount, 1);
  const signalPrefix = displayBuyCount && displaySellCount ? '⇅' : displayBuyCount ? '↑' : displaySellCount ? '↓' : '–';
  const tableBodyRows = `${tableRows}${referenceTableRows}`;
  const referenceRowNote = referenceEntries.length ? `；另列 ${visibleReferenceCount} 次模拟参考` : '';
  const signalClock = referenceOnly ? `历史参考（截取 ${referenceCapture}）` : `最近 ${activeWindow} 天`;
  const politiciansBody = `<div class="stock-signal">${signalPrefix} 本股${signalClock}政客披露：${signal} · ${buyPeople.size} 位买入、${sellPeople.size} 位卖出</div><section class="stock-data-section"><header class="stock-politician-intro"><div><b>政客交易明细</b><p>${referenceOnly ? '模拟参考' : 'STOCK Act / OGE 关联披露'} · 当前窗口 ${visibleTxs.length} 笔${referenceRowNote}</p></div><div class="stock-filter"><button data-stock-direction="all" class="${stockDirection === 'all' ? 'active' : ''}">全部</button><button data-stock-direction="purchase" class="${stockDirection === 'purchase' ? 'active' : ''}">买入</button><button data-stock-direction="sale" class="${stockDirection === 'sale' ? 'active' : ''}">卖出</button></div></header><div class="stock-direction"><div class="direction-summary-head"><b>政客买卖结构</b><span>${referenceOnly ? '模拟参考' : '按本窗口披露次数'} · 点击颜色筛选明细</span></div><div class="split-bar">${segments}</div><div class="balance-labels"><span class="buy">${displayBuyCount} Buy · ${(displayBuyCount / displayDirectionTotal * 100).toFixed(0)}%</span><span class="sell">${displaySellCount} Sell · ${(displaySellCount / displayDirectionTotal * 100).toFixed(0)}%</span></div></div><div class="stock-table-wrap">${tableBodyRows ? `<table class="stock-table stock-disclosure-table"><thead><tr><th>政客 / 官员</th><th>方向</th><th>金额</th><th>交易日</th><th>披露日</th></tr></thead><tbody>${tableBodyRows}</tbody></table>` : '<div class="stock-empty">当前筛选没有政客交易披露。</div>'}</div></section>`;
  // 标记画的是「这天有人买卖」，不是「这天成交在这个价」。所以这一段的措辞必须把两件事分开：
  // 头像落在交易日上，标记处的股价是当日收盘价，成交价不在披露范围内——红线在这里，
  // 少了这句，一张「头像 + 价格」的图很容易被读成这笔交易的成交价。
  const priceChartSvg = priceChart(market, allTickerTxs);
  const marksOmitted = priceChartGeo?.hidden || 0;
  const priceBody = `<section class="stock-price-section"><header class="stock-price-head"><h2>价格走势</h2><span>近 24 个月 · 日线收盘</span></header><div class="stock-price-frame">${priceChartSvg}</div><div class="stock-price-notes"><span>上季末（${market?.previous_quarter_end || '—'}）收盘约 <b>${market ? priceMoney(market.previous_quarter_end_price) : '—'}</b>，是本季涨跌的基准价，不是政客成交价。</span><span>头像标在披露人的<b>交易日</b>上，买在上、卖在下；标记显示的是该日<b>收盘价，不是这笔交易的成交价</b>。${marksOmitted ? `另有 ${marksOmitted} 笔披露在 24 个月窗口之外，未标出。` : ''}</span><span>行情截止 ${market?.as_of_date || '—'} · 来源 ${market?.price_source || '未提供'}。日线收盘价（EOD），非实时行情。</span></div></section>`;
  // 政客与价格已合并为一页：两块正文按「政客交易明细 → 价格走势」顺序同时渲染，没有页签。
  // 方向筛选（全部 / 买入 / 卖出）只筛政客那块，价格图不受影响。

  document.querySelectorAll('[data-stock-window]').forEach(b => b.classList.toggle('active', Number(b.dataset.stockWindow) === activeWindow));
  const cutoffLabel = referenceOnly ? `参考截取 ${referenceCapture}` : `数据截止 ${quarter.label} · 政客季度快照`;
  const trendLabel = referenceOnly ? '历史参考' : `本季 · ${trend}`;
  const quarterNote = referenceOnly
    ? '模拟参考不带新建仓、加仓、减仓或清仓信息，不计入本季。'
    : `仅采用申报中明确写出的调仓信息，不按买卖方向推断。${unclassifiedEffects ? `另有 ${unclassifiedEffects} 笔未分类记录未计入。` : ''}`;
  document.querySelector('#stockPageContent').innerHTML = `<section class="stock-hero"><div class="stock-heading"><div><div class="stock-id"><div class="stock-glyph">${ticker.slice(0,2)}</div><div><h1 class="stock-ticker">${ticker}</h1><div class="stock-company">${company}</div></div></div><div class="stock-cutoff">${cutoffLabel}</div></div><div><div class="stock-summary"><b>摘要</b><br>${summary}</div><div class="stock-status"><span>关注度 · ${attention}</span><span>${trendLabel}</span></div></div></div><div class="stock-metrics">${metrics.map(m => `<div class="stock-metric"><span>${m[0]}</span><b${m[3] ? ` class="${m[3]}"` : ''}>${m[1]}</b>${m[2] || ''}</div>`).join('')}</div><section class="stock-quarter" aria-label="本季政客调仓"><div class="stock-quarter-head">本季调仓 · 按披露人物去重</div><div class="stock-quarter-grid"><div class="stock-quarter-item"><span>新建仓</span><b class="positive">${newPositionPeople}位</b></div><div class="stock-quarter-item"><span>加仓</span><b class="positive">${increasePeople}位</b></div><div class="stock-quarter-item"><span>减仓 / 清仓</span><b class="negative">${reducePeople}位</b></div></div><p class="stock-quarter-note">${quarterNote}</p></section></section>${politiciansBody}${priceBody}<div class="stock-page-note">仅统计政客及官员的公开披露，不含机构持股、内部人交易；行情为独立展示的日终收盘价。交易可能迟报或修订，金额是区间，价格变化不代表政客本人收益。</div>`;

  document.querySelectorAll('[data-stock-direction]').forEach(b => b.addEventListener('click', () => { stockDirection = b.dataset.stockDirection; renderStockPage(); }));
  document.querySelectorAll('[data-stock-focus]').forEach(b => b.addEventListener('click', () => { stockDirection = b.dataset.stockFocus; renderStockPage(); }));
  document.querySelectorAll('[data-stock-tx]').forEach(row => row.addEventListener('click', () => openTransaction(row.dataset.stockTx)));
}

function closeDrawer() {
  document.querySelector('#drawer').classList.remove('open');
  document.querySelector('#drawerBackdrop').classList.remove('open');
  document.querySelector('#drawer').setAttribute('aria-hidden', 'true');
}

// 「国会山买入共识」：每个窗口一块，两块同时渲染。
// 不挂 data-window-panel——它不属于热门股票 / 交易明细这两个视图，两块恒显，不随 Tab 切换。
function renderBuyConsensus(days) {
  const rows = buyConsensus(windowTransactions(days), CONSENSUS_LIMIT);
  const marketByTicker = new Map((DATA.market_moves[String(days)] || []).map(row => [row.ticker, row]));
  document.querySelector(`#consensusGrid${days}`).innerHTML = rows.length ? rows.map(row => {
    const market = marketByTicker.get(row.ticker);
    const hasReturn = market && market.return_since_first_trade !== null && market.return_since_first_trade !== undefined && Number.isFinite(Number(market.return_since_first_trade));
    const returnValue = hasReturn ? Number(market.return_since_first_trade) : null;
    // 前三位各自一行：头像 + 姓名 + 职位与笔数 + 该人在这只股票上的买入区间。
    const buyers = row.buyers.slice(0, 3).map(buyer => {
      const person = PEOPLE[buyer.personId];
      if (!person) return '';
      const role = hasDisplayValue(person.role) ? `${person.role} · ` : '';
      // 头像与姓名合成一个按钮（这个人在本股的买入区间留在按钮外）：点了进人物页。
      // 它必须是 <button> 而不能靠整卡的点击监听——整卡进的是单股页，两个去处只能各有一个目标元素。
      return `<li>
        <button class="consensus-person" type="button" data-person-link="${person.id}" aria-label="查看 ${person.display_name} 个人主页">
          <img src="${person.portrait_data_uri}" alt="${person.display_name}">
          <span><b>${person.display_name}</b><small>${role}${buyer.count} 笔</small></span>
        </button>
        <b class="consensus-amount">${money(buyer.low)}–${money(buyer.high)}</b>
      </li>`;
    }).join('');
    // 整张卡进单股页：卡根挂 data-consensus-card，点击由下面那条监听接管（不用 data-ticker-link，
    // 那条委托监听在 capture 阶段就把事件吃掉，人物行上的 data-person-link 会一并被它拦走）。
    return `<article class="consensus-card" data-consensus-card="${row.ticker}">
      <div class="consensus-ticker">
        <b>${row.ticker}</b>
        <small>${row.company}</small>
        <span class="consensus-return ${hasReturn ? returnClass(returnValue) : ''}">${hasReturn ? pct(returnValue) : '—'}<small>${hasReturn ? '窗口涨跌' : '暂无可比窗口行情'}</small></span>
      </div>
      <div class="consensus-body">
        <div class="consensus-head">
          <b>买入合计 ${money(row.agg.low)}–${money(row.agg.high)}</b>
          <span class="consensus-pill">${row.buyerCount} 人买入</span>
        </div>
        <ol class="consensus-people">${buyers}</ol>
        <div class="consensus-foot">
          <span>共 ${row.agg.count} 笔 · 最新 ${dateLabel(row.agg.tradeDate)}买入 · ${dateLabel(row.agg.filedAt)}披露</span>
          <button class="consensus-link" type="button" data-ticker-link="${row.ticker}">单股页 →</button>
        </div>
      </div>
    </article>`;
  }).join('') : '<div class="consensus-empty">当前窗口没有买入披露记录。</div>';
  // 卡片整块可点的兜底：点到人物行或「单股页 →」时事件已被 capture 阶段的委托监听拦下，这里不会跑。
  // 剩余能走到这里的点击都落在卡的空白处，去单股页——与卡左栏那颗代码是同一个去处。
  document.querySelectorAll(`#consensusGrid${days} .consensus-card`).forEach(el => el.addEventListener('click', () => goTicker(el.dataset.consensusCard)));
}

// 看板两个窗口区块都渲染（两种视图都渲染，含隐藏的那一侧），切换视图即时生效。
function renderDashboardBlocks() {
  DASHBOARD_WINDOWS.forEach(days => { renderHotStocks(days); renderTimeline(days); renderBuyConsensus(days); });
}

// 顶部 30/90 只做区块锚点跳转，不改变数据；人物卡固定 DASHBOARD_WINDOW。
let anchorLockUntil = 0;

// 锚点落点与停靠栏底边之间留的空白。滚动监听的判据必须由它推出来：落点停在「停靠栏 + 这个值」
// 处，判据若比它小，点击 90 已经落在 90 区块上、高亮却还留在 30，正好差在阈值外面。
const ANCHOR_CLEARANCE = 16;

function markActiveWindow(days) {
  document.querySelectorAll('[data-window-anchor]').forEach(b => b.classList.toggle('active', Number(b.dataset.windowAnchor) === days));
}

// 停靠栏在文档流里，一压缩就把它上方的高度抽走，目标块随之上移——桌面 39px，窄屏最多 171px。
// 照压缩前的版面滚过去，落点就偏高，标题被压在停靠栏底下，恰恰是刚点完那个按钮的读者看不见它
// （520px 时实测标题落在栏下 −98px，读屏和视觉上都等于没跳过去）。所以先同步切到压缩态、量准
// 落点，再滚——整个过程在同一个任务里完成，中间不绘制，因而看不到闪动；滚动一开始观察器就真的
// 压上去，版面与量的时候一致，落点才准。
// 前提是压缩不能有高度过渡（见 .masthead 的 transition：只过渡 box-shadow）：高度一带过渡，
// 量到的就是压缩到一半的版面，事后补正又会和进行中的平滑滚动互相覆盖，怎么调都是偏的。
function scrollToWindowBlock(days) {
  markActiveWindow(days);
  const block = document.querySelector(`[data-window-block="${days}"]`);
  if (!block) return;
  // 平滑滚动是异步的，这期间滚动监听还在跑，会把高亮按「当前滚到哪」重算——于是刚点亮的目标
  // 在半路上又被改回另一个，快到终点才改回来。所以锁到滚动真正停下为止，而不是锁一个固定时长：
  // 这段距离 Chrome 要滚 1.4～1.6s，固定时长要么太短（途中乱跳）要么太长（读者接手后还锁着）。
  // 上限 2500ms 只是防呆，免得信号不来把高亮永久锁死。
  anchorLockUntil = Date.now() + 2500;
  const release = () => { anchorLockUntil = 0; syncWindowAnchor(); };
  const dock = document.querySelector('.masthead');
  if (!dock) {
    block.scrollIntoView({behavior: 'smooth', block: 'start'});
    holdAnchorLockUntilSettled(release);
    return;
  }
  dock.classList.add('is-stuck');
  document.body.classList.add('dock-stuck');
  // 量真实高度，不读 --dock-h：窄屏下压缩态的实际高度会超过这个变量（360px 时是 70px 对 60px）。
  const top = block.getBoundingClientRect().top + window.scrollY
            - dock.getBoundingClientRect().height - ANCHOR_CLEARANCE;
  // 不复原压缩态：读者正是要滚下去，观察器随后也会把它压上，复原只会在起滚那一帧闪一下高栏。
  window.scrollTo({top: Math.max(0, top), behavior: 'smooth'});
  holdAnchorLockUntilSettled(release);
}

// 滚动停住（连续四帧没动）再放手；读者自己滚也算停住，于是他一动手高亮就交还给他。
function holdAnchorLockUntilSettled(release) {
  let lastY = window.scrollY;
  let steady = 0;
  const tick = () => {
    const y = window.scrollY;
    steady = Math.abs(y - lastY) < 1 ? steady + 1 : 0;
    lastY = y;
    if (steady < 4 && Date.now() < anchorLockUntil) { requestAnimationFrame(tick); return; }
    release();
  };
  requestAnimationFrame(tick);
}

// 停靠栏常驻之后，30/90 的高亮必须反映「现在在看哪个窗口」，否则手动滚到 90 天区块时
// 高亮的仍是 30 天，而这块假高亮会一直挂在眼前。判据是 90 天区块的顶边有没有越过停靠栏。
// 上方的人物卡固定 30 天、共识块两个窗口并置，此时答案是 30 天，与卡片区实际展示的窗口一致。
function syncWindowAnchor() {
  if (Date.now() < anchorLockUntil) return;
  // 按文档顺序取首尾两个窗口块：首个正是 30/90 按钮跳转时命中的那块，末个是它之后的另一块，
  // 于是这里与 scrollToWindowBlock 用的是同一个判据，也不必写死 30/90、不依赖数组排列顺序。
  const blocks = document.querySelectorAll('[data-window-block]');
  const dock = document.querySelector('.masthead');
  if (!blocks.length || !dock) return;
  const lastBlock = blocks[blocks.length - 1];
  // 多留 8px 余量：落点正好停在停靠栏 + ANCHOR_CLEARANCE 处，贴着阈值会被取整误差翻回去；
  // 这点余量同时给了回滚方向的迟滞，读者往回滚几像素不会让高亮来回抖。
  const past = lastBlock.getBoundingClientRect().top <= dock.offsetHeight + ANCHOR_CLEARANCE + 8;
  markActiveWindow(Number(past ? lastBlock.dataset.windowBlock : blocks[0].dataset.windowBlock));
}

function render() {
  const txs = windowTransactions(DASHBOARD_WINDOW);
  renderPriority(txs); renderDashboardView(); renderDashboardBlocks();
}

document.addEventListener('click', event => {
  const personLink = event.target.closest('[data-person-link]');
  if (!personLink) return;
  event.preventDefault();
  event.stopPropagation();
  openPerson(personLink.dataset.personLink);
}, true);

// 单股页的唯一入口：人物页开着时按「从人物页进单股页」走（保留返回栈），否则直接从看板进。
// 委托监听与共识卡的整卡点击都调它，两条路才不会分叉。
function goTicker(ticker) {
  if (document.querySelector('#personPage').classList.contains('open')) openTickerFromPerson(ticker);
  else openTicker(ticker);
}

// capture 阶段：先于卡片自己的点击监听跑，并 stopPropagation，于是「卡里点了代码」不会再多跳一层人物页。
document.addEventListener('click', event => {
  const tickerLink = event.target.closest('[data-ticker-link]');
  if (!tickerLink) return;
  event.preventDefault();
  event.stopPropagation();
  goTicker(tickerLink.dataset.tickerLink);
}, true);

/* 价格走势的悬浮读数。浮层挂在 body 上而不是图里：.stock-price-frame 是 overflow-x: auto，
   放进去的绝对定位元素会撑出纵向滚动条；fixed + clientX/clientY 则连单股页自身的滚动都不用管。
   监听绑在 document 上而不是每帧重绑：整页 innerHTML 每渲一次就重建，绑在图上的监听会一起没。
   这是鼠标才有的通路，所以浮层对读屏隐藏（aria-hidden），同样的信息写在 svg 的 aria-label 里。 */
let priceTipEl = null;
function priceTip() {
  if (!priceTipEl) {
    priceTipEl = document.createElement('div');
    priceTipEl.className = 'price-tip';
    priceTipEl.setAttribute('aria-hidden', 'true');
    document.body.appendChild(priceTipEl);
  }
  return priceTipEl;
}
function hidePriceTip() {
  if (priceTipEl) priceTipEl.classList.remove('is-on');
  // 游标和浮层同生共死：浮层收起了、绿点还留在图上，会被读成一个「当前选中」的静态标记。
  const cursor = priceChartGeo?.cursor;
  if (cursor) cursor.group.classList.remove('is-on');
}
/* 悬浮游标：跟着鼠标在曲线上走的绿点 + 竖直虚线，样式与季度基准点同源，读起来是「同一套指示」。
   它和季度线是两个东西——季度线是**静态**的涨跌基准价参考，指针移开也不能跑掉；游标只在指针
   位于绘图区内时出现。
   绿点吸附到**最近的日线点**而不是随鼠标连续滑动：点必须落在曲线上、且必须和浮层报出的那个
   收盘价是同一个点，否则同一屏里点、线、数字三者说的日期会对不上。
   节点缓存在 geo.cursor 上：mousemove 是高频事件，每次 querySelector 是白花的开销；geo 本身
   每次重渲都会重建，缓存也就跟着失效，不会悬到上一份 DOM 上。 */
function setPriceCursor(svg, geo, index) {
  if (!geo.cursor) {
    const group = svg.querySelector('.price-cursor');
    if (!group) return;
    geo.cursor = {group, line: group.querySelector('.price-cursor-line'), dot: group.querySelector('.price-cursor-dot')};
  }
  const cursor = geo.cursor;
  if (index === null) { cursor.group.classList.remove('is-on'); return; }
  const px = (geo.left + index / (geo.series.length - 1) * geo.plotWidth).toFixed(2);
  const py = (geo.top + (geo.max - geo.series[index].close) / Math.max(geo.max - geo.min, 1) * geo.plotHeight).toFixed(2);
  cursor.line.setAttribute('x1', px);
  cursor.line.setAttribute('x2', px);
  cursor.dot.setAttribute('cx', px);
  cursor.dot.setAttribute('cy', py);
  cursor.group.classList.add('is-on');
}
function priceMarkTip(mark) {
  const side = mark.kind === 'buy' ? '<span class="tip-buy">买入</span>' : '<span class="tip-sell">卖出</span>';
  const who = id => PEOPLE[id]?.display_name || id;
  // 折叠列表里每一笔都要带上自己的日期和收盘价：折叠只是不再逐个画头像，
  // 不是把这几笔变成一行汇总。
  if (mark.folded) return `<b>另有 ${mark.folded.length} 笔未展开</b>${mark.folded.map(item => `<br>${side} ${who(item.tx.person_id)} ${range(item.tx)}<br>${item.tx.transaction_date} · 收盘 ${priceMoney(item.close)}`).join('')}<small>收盘价不是成交价</small>`;
  return `<b>${who(mark.tx.person_id)}</b> · ${side}<br>${mark.tx.transaction_date} · 当日收盘 ${priceMoney(mark.close)}<br>金额 ${range(mark.tx)}<small>标记在交易日上；收盘价不是这笔的成交价</small>`;
}
document.addEventListener('mousemove', event => {
  const svg = event.target.closest?.('.stock-price-chart');
  const geo = priceChartGeo;
  if (!svg || !geo) return hidePriceTip();
  const tip = priceTip();
  // 指针在 SVG 用户坐标里的位置。用 getScreenCTM 的逆矩阵，而不是自己按宽高比缩放——
  // 图有 min-height，窄宽度下 preserveAspectRatio 会在上下留白，手算的比例在那里是错的。
  // 两条通路（买卖点、曲线上任意点）都要这个值，所以提到分支外面算一次。
  const local = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
  const inside = local.x >= geo.left && local.x <= geo.left + geo.plotWidth;
  const step = geo.plotWidth / (geo.series.length - 1);
  // 夹紧到 [0, n-1]：指针压在绘图区左右边缘时，四舍五入会把索引推出区间。
  const index = Math.min(geo.series.length - 1, Math.max(0, Math.round((local.x - geo.left) / step)));
  const markEl = event.target.closest?.('[data-price-mark]');
  // 悬停头像时游标也吸附：头像的 x 就是它交易日的 x，游标正好落在同一天上，「谁买的」和
  // 「那天的价」就在同一条竖线里。指针跑到绘图区外（头像圈压在边缘上）就不再挪游标，
  // 免得它跳到离指针很远的一天上去。
  setPriceCursor(svg, geo, (inside || markEl) ? index : null);
  if (markEl) {
    const mark = geo.marks[Number(markEl.dataset.priceMark)];
    if (!mark) return hidePriceTip();
    tip.innerHTML = priceMarkTip(mark);
  } else {
    if (!inside) return hidePriceTip();
    const point = geo.series[index];
    tip.innerHTML = `<b>${point.date}</b><br>收盘 ${priceMoney(point.close)}`;
  }
  tip.classList.add('is-on');
  const box = tip.getBoundingClientRect();
  tip.style.left = `${Math.max(12, Math.min(event.clientX + 16, window.innerWidth - box.width - 12))}px`;
  tip.style.top = `${Math.max(12, event.clientY + 16 + box.height > window.innerHeight ? event.clientY - 16 - box.height : event.clientY + 16)}px`;
});
// 鼠标不动、页面自己变的时候（点筛选、滚动离开）读数会停在旧值上，所以这两件事发生时直接收起。
document.addEventListener('click', hidePriceTip);
document.addEventListener('scroll', hidePriceTip, true);

document.querySelector('#cutoff').textContent = `数据截点 ${DATA.meta.data_cutoff_at.replace('T',' ')} · ${DATA.meta.timezone}`;
document.querySelectorAll('[data-window-view]').forEach(button => button.addEventListener('click', () => { dashboardView = button.dataset.windowView; renderDashboardView(); }));
// 顶部 30/90 是锚点：只滚动到对应区块，不改变 activeWindow，也不重算看板。
document.querySelectorAll('[data-window-anchor]').forEach(b => b.addEventListener('click', () => scrollToWindowBlock(Number(b.dataset.windowAnchor))));
// 哨兵离开视口顶端 = 停靠栏开始吸附。用 IntersectionObserver 而不是每帧读位置：
// 读 getBoundingClientRect 会强制同步布局，而这个判断只需要在跨越那一点时发生一次。
new IntersectionObserver(([entry]) => {
  const stuck = !entry.isIntersecting;
  document.querySelector('.masthead').classList.toggle('is-stuck', stuck);
  // --dock-h 挂在 body 上：窗口区块的 scroll-margin-top 靠它跟着停靠栏一起收。
  document.body.classList.toggle('dock-stuck', stuck);
}, {threshold: 0}).observe(document.querySelector('.dock-sentinel'));
// 滚动只用来同步 30/90 的高亮，所以按帧节流，且不阻止默认滚动。
let anchorFrame = 0;
window.addEventListener('scroll', () => {
  if (anchorFrame) return;
  anchorFrame = requestAnimationFrame(() => { anchorFrame = 0; syncWindowAnchor(); });
}, {passive: true});
syncWindowAnchor();
document.querySelectorAll('[data-stock-window]').forEach(b => b.addEventListener('click', () => { activeWindow = Number(b.dataset.stockWindow); stockDirection = 'all'; render(); renderStockPage(); }));
document.querySelectorAll('[data-person-window]').forEach(b => b.addEventListener('click', () => { activeWindow = Number(b.dataset.personWindow); personDirection = 'all'; render(); renderPersonPage(); }));
document.querySelector('#stockBack').addEventListener('click', handleStockBack);
document.querySelector('#personBack').addEventListener('click', () => { closePersonPage(); render(); });
// 搜索／方向／交易类型是每块各自一套的筛选状态：只重渲染被改的那一块，另一块不动。
DASHBOARD_WINDOWS.forEach(days => {
  const block = document.querySelector(`#window-${days}`);
  if (!block) return;
  block.querySelector('.timeline-search input').addEventListener('input', e => { timelineFilters[days].query = e.currentTarget.value; renderTimeline(days); });
  block.querySelectorAll('[data-tx-filter]').forEach(b => b.addEventListener('click', () => {
    timelineFilters[days].direction = b.dataset.txFilter;
    block.querySelectorAll('[data-tx-filter]').forEach(item => item.classList.toggle('active', item === b));
    renderTimeline(days);
  }));
  block.querySelector('.instrument-filter').addEventListener('change', e => { timelineFilters[days].instrument = e.currentTarget.value; renderTimeline(days); });
});
document.querySelector('#drawerClose').addEventListener('click', closeDrawer);
document.querySelector('#drawerBackdrop').addEventListener('click', closeDrawer);
document.addEventListener('keydown', e => { if (e.key !== 'Escape') return; if (document.querySelector('#drawer').classList.contains('open')) closeDrawer(); else if (document.querySelector('#personPage').classList.contains('open')) closePersonPage(); else if (document.querySelector('#stockPage').classList.contains('open')) handleStockBack(); });
bindHotSort();
renderSources(); render();
</script>
</body>
</html>'''


def render_html(data: dict) -> str:
    is_demo = data.get("meta", {}).get("is_demo") is True
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    replacements = {
        "__DATA__": payload,
        "__DEMO_BADGE__": '<span class="demo-badge">模拟数据</span>' if is_demo else "",
        "__BUILD_LABEL__": "SIMULATED DATA" if is_demo else "OFFICIAL DISCLOSURE DATA",
        "__PAGE_TITLE__": "Dashboard",
        "__FOOTNOTE__": (
            "模拟展示：交易、持仓与行情均为虚构，个人页另含公开页面的冻结汇总参考并已单独标注。不构成投资建议。"
            if is_demo
            else "本页整理官方公开申报（OGE / House Clerk / Senate eFD）与独立日终证券价格，不构成投资建议。金额为披露区间，申报可能延迟或修订；日线收盘价非实时行情，价格变化不代表相关人物的收益或当前仓位。"
        ),
    }
    html = HTML_TEMPLATE
    for marker, value in replacements.items():
        html = html.replace(marker, value)
    return html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    data = load_dashboard_data(args.input.resolve())
    html = render_html(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Rendered {args.output.resolve()} ({len(html):,} characters)")


if __name__ == "__main__":
    main()
