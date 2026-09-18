from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
DEMO_PATH = Path(__file__).resolve().parent.parent / "assets" / "review-dashboard-v2.json"
sys.path.insert(0, str(SCRIPT_DIR))

from process_snapshot import ProcessingError, build_snapshot  # noqa: E402
from fetch_snapshot import build_request  # noqa: E402
from render_dashboard import render_html  # noqa: E402


def production_payload() -> dict:
    return {
        "meta": {
            "snapshot_id": "test-production-1",
            "data_cutoff_at": "2026-09-11T21:00:00-04:00",
            "timezone": "America/New_York",
            "is_demo": False,
        },
        "people": [
            {
                "id": "house:P000197",
                "display_name": "Test Official",
                "role": "House member",
                "chamber": "House",
                "party": "D",
                "state": "CA",
                "priority": True,
            }
        ],
        "transactions": [
            {
                "id": "house:file-1:row-1",
                "filing_id": "file-1",
                "person_id": "house:P000197",
                "owner": "Spouse",
                "asset_name": "Example Corp",
                "ticker": "TEST",
                "instrument_type": "Stock",
                "transaction_type": "purchase",
                "transaction_date": "2026-08-20",
                "filed_at": "2026-09-01T10:00:00-04:00",
                "amount_low": 1001,
                "amount_high": 15000,
                "position_effect": "increase",
                "position_effect_basis": "matched_holding_comparison",
                "source_id": "house_clerk",
                "source": "U.S. House Clerk",
                "source_url": "https://disclosures-clerk.house.gov/test.pdf",
                "verification_status": "official_matched",
            }
        ],
        "reported_holdings": [
            {
                "id": "house:file-2:holding-1",
                "filing_id": "file-2",
                "person_id": "house:P000197",
                "owner": "Spouse",
                "asset_name": "Example Corp",
                "ticker": "TEST",
                "instrument_type": "Stock",
                "report_period_end": "2025-12-31",
                "filed_at": "2026-05-15T10:00:00-04:00",
                "value_low": 15001,
                "value_high": 50000,
                "change_from_prior": "range_increased",
                "source_id": "house_clerk",
                "source": "U.S. House Clerk",
                "source_url": "https://disclosures-clerk.house.gov/holding.pdf",
                "verification_status": "official_matched",
            }
        ],
        "security_market_data": [
            {
                "ticker": "TEST",
                "company": "Example Corp",
                "source_id": "alpaca_sip_eod",
                "price_source": "Alpaca SIP EOD",
                "feed": "sip",
                "timeframe": "1Day",
                "adjustment": "split",
                "price_history": [
                    {"date": "2026-06-30", "close": 100.0},
                    {"date": "2026-08-20", "close": 104.0},
                    {"date": "2026-09-01", "close": 105.0},
                    {"date": "2026-09-11", "close": 110.0},
                ],
            }
        ],
        "source_health": [
            {
                "source_id": "house_clerk",
                "source": "U.S. House Clerk",
                "status": "ok",
                "last_checked_at": "2026-09-11T20:59:00-04:00",
            },
            {
                "source_id": "alpaca_sip_eod",
                "source": "Alpaca SIP EOD",
                "status": "ok",
                "last_checked_at": "2026-09-11T20:59:00-04:00",
            },
        ],
    }


class ProcessSnapshotTests(unittest.TestCase):
    def test_builds_frontend_projections_from_canonical_rows(self) -> None:
        result = build_snapshot(production_payload())
        self.assertEqual(result["meta"]["schema_version"], "politician-dashboard/v1")
        self.assertEqual(result["summary"]["transactions_30d"], 1)
        self.assertEqual(result["market_moves"]["30"][0]["buy_count"], 1)
        self.assertEqual(result["market_moves"]["30"][0]["first_transaction_date"], "2026-08-20")
        self.assertEqual(result["market_moves"]["30"][0]["return_baseline_date"], "2026-08-20")
        self.assertEqual(result["market_moves"]["30"][0]["price_as_of_date"], "2026-09-11")
        self.assertEqual(result["security_market_data"][0]["current_price"], 110.0)
        self.assertEqual(result["security_market_data"][0]["previous_quarter_end_price"], 100.0)
        self.assertEqual(result["transactions"][0]["disclosure_lag_days"], 12)
        self.assertEqual(result["transactions"][0]["underlying_return_since_filing"], 4.76)
        self.assertEqual(
            result["source_health"][0]["source_url"],
            "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch",
        )

    def test_rejects_non_allowlisted_disclosure_source(self) -> None:
        payload = production_payload()
        payload["transactions"][0]["source_id"] = "sec_edgar_13f"
        with self.assertRaisesRegex(ProcessingError, "Forbidden transaction source_id"):
            build_snapshot(payload)

    def test_rejects_production_row_without_official_link(self) -> None:
        payload = production_payload()
        payload["transactions"][0]["source_url"] = None
        with self.assertRaisesRegex(ProcessingError, "requires HTTPS source_url"):
            build_snapshot(payload)

    def test_rejects_demo_reference_snapshot_in_production(self) -> None:
        demo = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
        payload = production_payload()
        payload["people"][0]["demo_reference_snapshot"] = demo["people"][0]["demo_reference_snapshot"]
        with self.assertRaisesRegex(ProcessingError, "forbidden in production"):
            build_snapshot(payload)

    def test_rejects_demo_priority_fixture_in_production(self) -> None:
        payload = production_payload()
        payload["people"][0].update(
            {"demo_priority_rank": 1, "demo_priority_count": 174, "demo_priority_group": "白宫"}
        )
        with self.assertRaisesRegex(ProcessingError, "demo_priority_.*forbidden in production"):
            build_snapshot(payload)

    def test_rejects_non_sip_or_non_split_market_contract(self) -> None:
        payload = production_payload()
        payload["security_market_data"][0]["source_id"] = "iex"
        with self.assertRaisesRegex(ProcessingError, "Forbidden market source_id"):
            build_snapshot(payload)
        payload = production_payload()
        payload["security_market_data"][0]["adjustment"] = "raw"
        with self.assertRaisesRegex(ProcessingError, "adjustment=split"):
            build_snapshot(payload)

    def test_third_party_position_classification_is_not_used(self) -> None:
        payload = copy.deepcopy(production_payload())
        payload["transactions"][0]["position_effect_basis"] = "vendor_classification"
        result = build_snapshot(payload)
        self.assertEqual(result["transactions"][0]["position_effect"], "unknown")
        self.assertIsNone(result["transactions"][0]["position_effect_basis"])

    def test_production_renderer_uses_the_same_canonical_snapshot(self) -> None:
        result = build_snapshot(production_payload())
        result["people"][0]["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        self.assertIn("OFFICIAL DISCLOSURE DATA", html)
        self.assertIn("White House Stock Tracker · Dashboard", html)
        self.assertIn("国会山重点关注", html)
        self.assertIn("国会山热门关注股票", html)
        self.assertIn("标的交易明细", html)
        self.assertNotIn(">热门标的关注<", html)
        self.assertNotIn(">标的买卖结构<", html)
        self.assertIn("最新一期数据源", html)
        self.assertIn("t.transaction_type === 'purchase' ? '买入'", html)
        self.assertNotIn('id="holdings"', html)
        self.assertNotIn('id="roster"', html)
        self.assertNotIn("renderRoster", html)
        self.assertNotIn("<div class=\"demo-strip\">", html)
        self.assertNotIn("__DATA__", html)

    def test_demo_renderer_uses_formal_dashboard_chrome(self) -> None:
        result = build_snapshot(production_payload())
        result["meta"]["is_demo"] = True
        result["people"][0]["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        self.assertIn('<span class="demo-badge">模拟数据</span>', html)
        self.assertNotIn("INTERNAL REVIEW", html)
        self.assertNotIn("review 10", html)
        self.assertNotIn("视频模式", html)
        self.assertIn("--bg: #f4f1e8", html)
        self.assertIn("--panel: #ffffff", html)
        self.assertIn("font: 16px/1.6 var(--sans)", html)
        self.assertNotIn("--bg: #080b0d", html)
        # 语义色的描边／底色必须与 --buy/--sell/--signal 同源。
        # 这几处曾残留暗色主题的亮青绿、亮粉与暗橄榄，与亮色调色板不同色相，已抽成变量。
        for tint in (
            "--signal-edge: rgba(166,99,14,.34)",
            "--buy-edge: rgba(8,127,91,.34)",
            "--sell-edge: rgba(197,63,77,.34)",
            "--buy-wash: rgba(8,127,91,.12)",
            "--sell-wash: rgba(197,63,77,.12)",
        ):
            self.assertIn(tint, html, f"missing light-palette tint {tint}")
        for dark_leftover in ("#56491e", "rgba(83,210,154", "rgba(255,102,112"):
            self.assertNotIn(dark_leftover, html, f"dark-theme leftover {dark_leftover} still present")
        # demo／production 的可见差异由 demo-badge、build label、footnote 三个真实出口承担；
        # body 上的模式类名既无 CSS 规则也无 JS 读取，已作为死钩子移除。
        self.assertIn("<body>", html, "body tag should carry no mode class")
        for dead_hook in ("demo-mode", "production-mode", "__BODY_CLASS__"):
            self.assertNotIn(dead_hook, html, f"dead mode hook {dead_hook} still present")

    def test_trump_demo_reference_is_valid_and_visible(self) -> None:
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        trump = next(person for person in result["people"] if person["id"] == "sim-donald-trump")
        self.assertEqual(trump["demo_reference_snapshot"]["disclosed_transactions"], 4395)
        self.assertEqual(trump["demo_reference_snapshot"]["performance_sample_1y"], 206)
        self.assertIn("交易类型", html)
        self.assertNotIn("交易工具", html)
        self.assertIn("referenceEntries", html)
        self.assertIn("所有人未提供 · 模拟参考", html)
        # 模拟参考在人物页只剩表现观察的三格；逐笔卡片墙已删除，但单股页的
        # referenceEntries 仍读 notable_transactions（按 ticker 取冻结样本），所以只锁人物页的出口。
        self.assertIn("reference.performance_sample_1y", html)
        self.assertIn("reference.median_return_1y_pct", html)
        self.assertIn("reference.direction_match_pct", html)
        self.assertNotIn('<h3>大额披露样本</h3>', html,
                         "模拟参考的逐笔卡片墙仍在：它和表现观察的三格重复，且会挤掉明细表")
        self.assertNotIn('${reference.notable_transactions.length}', html,
                         "模拟参考的逐笔卡片计数残留：卡片墙已删，这个计数没有出口")

    def test_demo_priority_directory_and_dashboard_tabs_match_review_contract(self) -> None:
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        priority = sorted(
            (person for person in result["people"] if person.get("priority")),
            key=lambda person: person["demo_priority_rank"],
        )
        self.assertEqual(
            [person["display_name"] for person in priority],
            [
                "Donald J Trump",
                "Thomas H Tuberville",
                "Markwayne Mullin",
                "Scott A Kupor",
                "Nancy Pelosi",
                "Scott H. Peters",
                "Steve Cohen",
                "Alan Armstrong",
                "April McClain Delaney",
                "Daniel Crenshaw",
                "Laurel Lee",
                "Jared Isaacman",
                "Tony Wied",
                "Frank J Bisignano",
            ],
        )
        self.assertEqual(
            [person["demo_priority_count"] for person in priority],
            [174, 21, 8, 8, 6, 5, 3, 3, 2, 2, 2, 2, 1, 1],
        )
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        # 视图切换条从页级一条变成每块各一条（30 天、90 天各 2 个按钮），两条联动：
        # 共用一份 dashboardView 状态，不是两套互不相干的控件。
        self.assertEqual(html.count('data-window-view="'), 4)
        self.assertEqual(html.count('data-window-panel="'), 4)
        # 选择器字符串和标记必须成对钉死：改了一边不改另一边，按钮就会静默失效
        # （点下去没反应，也不报错），这类失配只能靠把两者写进同一条断言挡住。
        self.assertIn("document.querySelectorAll('[data-window-view]')", html)
        self.assertIn('data-window-view="hot-stocks"', html)
        self.assertIn('data-window-view="timeline"', html)
        self.assertIn('let dashboardView = \'hot-stocks\';', html)
        self.assertIn('function renderDashboardView()', html)
        self.assertNotIn('data-dashboard-tab=', html)
        self.assertNotIn('renderDashboardTabs', html)
        self.assertIn('data-window-panel="hot-stocks"', html)
        self.assertIn('data-window-panel="timeline"', html)
        self.assertNotIn('data-window-panel="consensus"', html)
        self.assertNotIn('data-window-panel="market"', html)
        self.assertIn('class="hot-stock-avatar-stack"', html)
        # 头像堆叠里的悬停反馈必须画在按钮自己的 box-shadow 上，不能沿用全局那条
        # .avatar-person-link:hover img 的 outline：按钮要 overflow: hidden 才能把方形肖像
        # 裁成圆，溢出裁剪正好切在 img 的边框盒上，那条 outline 整圈被裁掉，悬停毫无变化。
        self.assertIn('.hot-stock-avatar-stack .avatar-person-link:hover,\n  .hot-stock-avatar-stack .avatar-person-link:focus-visible { z-index: 2;', html,
                      msg="头像堆叠的悬停特效没了")
        stack_hover = html.split('.hot-stock-avatar-stack .avatar-person-link:focus-visible {', 1)[1].split('}', 1)[0]
        self.assertIn('box-shadow: 0 0 0 2px var(--signal)', stack_hover,
                      msg="堆叠里的悬停描边不再走 box-shadow：img 上的 outline 会被 overflow: hidden 裁掉")
        self.assertIn('z-index: 2', stack_hover,
                      msg="悬停的头像没有抬到相邻头像之上：描边会被 DOM 在后的头像压掉一半")
        self.assertIn('class="split-bar"', html)
        self.assertIn('窗口首笔交易日 → 行情截止日', html)
        self.assertIn('本窗口首笔交易日至今的收盘价变化，不代表政客本人收益', html,
                      msg="热门股票表缺「涨跌不是政客收益」的说明：这张表会被读成收益率")
        self.assertIn("market?.return_baseline_date", html)
        self.assertIn('function renderHotStocks(days)', html)
        self.assertNotIn('function renderConsensus(txs)', html)
        self.assertNotIn('function renderMarketMoves(txs)', html)
        self.assertIn('days - 1', html)
        self.assertNotIn('activeWindow - 1', html)
        self.assertNotIn('id="roster"', html)
        self.assertNotIn("renderRoster", html)
        self.assertNotIn('class="action-line"', html)
        self.assertNotIn('class="hero-stats"', html)
        self.assertNotIn('class="content-grid"', html)
        self.assertNotIn('class="side-stack"', html)

    def test_dashboard_renders_both_window_blocks_and_anchors(self) -> None:
        # 30/90 两个窗口同时呈现：顶部按钮退化为锚点，不再切换数据。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        for days in ("30", "90"):
            self.assertIn(f'id="hotStocks{days}"', html)
            self.assertIn(f'id="hotStockReturnScope{days}"', html)
            self.assertIn(f'id="timeline{days}"', html)
            self.assertIn(f'id="timelineCount{days}"', html)
            self.assertIn(f'id="timelineSearch{days}"', html)
            self.assertIn(f'id="instrumentFilter{days}"', html)
            self.assertIn(f'data-window-anchor="{days}"', html)
            # 每个窗口块 = 大标题 + 一条自己的视图切换条 + 一张表：块本身只出现一次。
            # 只数脚本之前的标记：这条断言防的是「别的元素复用了这个属性」——共识块一旦复用，
            # 顶部 30/90 锚点就会改跳到共识块而不是表格——而脚本里出现的同名字符串是选择器、
            # 不是元素。按整份文件数子串会让一处合法的选择器把断言顶红，掩盖真正的复用。
            self.assertEqual(html.split('<script', 1)[0].count(f'data-window-block="{days}"'), 1,
                             f"{days} 天窗口块应只有一个")
            self.assertIn(f'id="window-{days}"', html)
            # 大标题必须在 .panel 外面，不能退回成表格内的第一行。
            block = html.split(f'id="window-{days}"', 1)[1].split('class="window-block"', 1)[0]
            self.assertLess(block.index('window-block-title'), block.index('class="panel"'),
                            f"{days} 天窗口标题必须排在表格 .panel 之前（标题在表外）")
        # 旧的互斥切换控件必须彻底消失，否则会出现两个行为不同的同名按钮。
        self.assertNotIn('data-window="30"', html)
        self.assertNotIn('data-window="90"', html)
        self.assertNotIn('id="hotStocks"', html)
        self.assertNotIn('id="timeline"', html)
        self.assertIn('const DASHBOARD_WINDOW = 30;', html)
        self.assertIn('const DASHBOARD_WINDOWS = [30, 90];', html)
        self.assertIn('function renderDashboardBlocks()', html)
        self.assertIn('function scrollToWindowBlock(days)', html)
        self.assertIn('function windowTransactions(days)', html)
        # 人物卡固定 30 天口径，不随锚点按钮变化。顶部那条 5 格横条指标（活跃人物/买入/卖出/
        # 披露金额区间/披露滞后中位数）已按评审结论整块删除，它有自己独立的 grid、响应式断点和
        # renderMetrics 入口，删干净才算真的不在了——残留一半会以 0 行高的空壳留在页面顶部。
        self.assertIn('const txs = windowTransactions(DASHBOARD_WINDOW);', html)
        self.assertNotIn('id="metrics"', html)
        self.assertNotIn('function renderMetrics(txs)', html)
        self.assertNotIn('renderMetrics(', html)
        # 视图切换条联动（两块一起切），筛选却是每块各自一套（只重渲染自己那块）。
        # 这两条必须同时成立：只联动不独立会让两块被同一份筛选绑死，
        # 只独立不联动则会在同屏出现一块看股票、一块看明细的错配。
        self.assertEqual(html.count('DASHBOARD_WINDOWS.forEach(renderTimeline)'), 0)
        self.assertIn('let dashboardView = \'hot-stocks\';', html)
        self.assertIn("button.dataset.windowView === dashboardView", html)
        self.assertIn('timelineFilters[days].query = e.currentTarget.value; renderTimeline(days);', html)
        self.assertIn('const timelineFilters = {', html)
        self.assertNotIn('timelineQuery', html)
        self.assertNotIn('activeDirection', html)
        self.assertNotIn('activeInstrument', html)

    def test_masthead_dock_stays_pinned_and_lands_the_anchor(self) -> None:
        # 顶部停靠栏常驻 + 30/90 锚点。这几条都是一改就静默坏掉的契约：栏一压缩就没收
        # 文档流上方的高度，落点、判据、按钮标签宽度全跟着挪位，但页面不会报任何错。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        # 常驻：粘顶、层级压在三个全屏浮层之下（人物页 75 / 单股页 70 / 抽屉 80、90）。
        self.assertIn('.masthead { position: sticky; top: 0; z-index: 30;', html,
                      msg="停靠栏必须粘顶，且层级要低于全屏浮层")
        self.assertIn('class="dock-sentinel"', html, msg="粘顶判据的哨兵元素不在")
        self.assertIn("'.dock-sentinel'", html, msg="观察器没有盯住哨兵")
        self.assertIn("classList.toggle('is-stuck', stuck)", html, msg="压缩态没有跟着哨兵切换")
        self.assertIn("document.body.classList.toggle('dock-stuck', stuck)", html,
                      msg="--dock-h 没有跟着压缩态切换")
        self.assertIn('body.dock-stuck { --dock-h: 60px; }', html, msg="压缩态高度变量不在")
        # 高度与内边距绝不能进过渡：压缩必须落在施加它的那一帧里，否则量落点时量到的是
        # 压缩到一半的版面，而事后再补正会被仍在进行中的平滑滚动覆盖掉，怎么调都是偏的。
        masthead = html.split('.masthead {', 1)[1].split('}', 1)[0]
        self.assertIn('transition: box-shadow .18s ease;', masthead,
                      msg="停靠栏只应过渡 box-shadow")
        for prop in ('min-height .18s', 'padding .18s'):
            self.assertNotIn(prop, masthead, msg=f"停靠栏过渡里不能有 {prop}")
        # 落点：先切压缩态量准，再滚到「栏底边 + 这个常量」；判据必须由同一个常量推出来，
        # 否则点击 90 停在 90 区块上、高亮却还留在 30。
        self.assertIn('const ANCHOR_CLEARANCE = 16;', html, msg="落点留白常量不在")
        self.assertIn('dock.classList.add(\'is-stuck\');', html, msg="量落点前没有先切压缩态")
        self.assertIn('dock.getBoundingClientRect().height - ANCHOR_CLEARANCE', html,
                      msg="落点没有按停靠栏底边 + 留白算")
        self.assertIn('dock.offsetHeight + ANCHOR_CLEARANCE + 8', html,
                      msg="滚动监听的判据没有由同一个留白常量推出")
        # 高亮锁到滚动真正停下为止，不是固定时长：这段距离要滚 1.4～1.6s。
        self.assertIn('function holdAnchorLockUntilSettled(release)', html,
                      msg="高亮没有锁到滚动停下")
        self.assertIn('release();', html, msg="滚动停下后没有交还高亮")
        # 按钮标签里的空格是天然断点，栏一压缩按钮先被挤，不禁止换行就会折成两行、
        # 把栏高度又顶回去，正好抵消压缩省下的空间。取的是基础规则（多行那条），
        # 不是 .masthead .segmented button 覆盖规则——按前者切会先撞上后者。
        segmented = html.split('.segmented button {\n', 1)[1].split('}', 1)[0]
        self.assertIn('white-space: nowrap;', segmented,
                      msg="切换按钮标签必须禁止换行，否则压缩态下折行把栏高度顶回去")
        # 顺序即契约：先切压缩态、再量落点、最后才滚。倒过来就是按压缩前的版面算落点，
        # 窄屏能把窗口标题压到停靠栏底下（520px 实测 −98px）。
        body = html.split('function scrollToWindowBlock(days) {', 1)[1].split('\n}', 1)[0]
        self.assertLess(body.index("dock.classList.add('is-stuck');"),
                        body.index('const top = block.getBoundingClientRect().top'),
                        msg="必须先切到压缩态再量落点")
        self.assertLess(body.index('const top = block.getBoundingClientRect().top'),
                        body.index('window.scrollTo({top: Math.max(0, top)'),
                        msg="必须先量准落点再滚")

    def test_hot_stock_table_carries_its_own_sort_controls(self) -> None:
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        # 热门股票改成真表：表头住进 <thead>，行由 JS 填进 <tbody>。表头文字不变，
        # 变的是每格多了一枚排序按钮。div 版的 .hot-stock-head / .hot-stock-list 必须消失，
        # 残留一半会留下一排不参与表格布局的表头。
        for days in ("30", "90"):
            self.assertIn(f'<table class="hot-stock-table" id="hotStockTable{days}">', html,
                          msg=f"{days} 天热门股票表缺 <table> 外壳：排序按钮没有表头可住")
            self.assertIn(f'<tbody id="hotStocks{days}"></tbody>', html,
                          msg=f"{days} 天热门股票的行容器必须是空 <tbody>，由 JS 填行")
        self.assertNotIn('class="hot-stock-head"', html,
                         msg="旧的 div 表头还在：它会和新表头同时出现在页面上")
        self.assertNotIn('class="hot-stock-list"', html,
                         msg="旧的 div 行容器还在：它不再参与表格布局，行会脱离表头对齐")
        # 表头内容按用户要求暂时不变，四列一字不改，只是每列挂一枚排序按钮。
        for label in ("股票 / 公司", "关联人物", "买入 / 卖出", "股价涨跌"):
            self.assertIn(f">{label}</button>", html, msg=f"表头「{label}」文案变了或没挂排序按钮")
        for key in ("ticker", "people", "amount", "return"):
            self.assertEqual(html.count(f'data-hs-sort="{key}"'), 2,
                             f"排序键 {key} 应在 30/90 两块各有一枚按钮")
        self.assertIn('aria-sort="descending"', html,
                      msg="排序按钮必须给出 aria-sort，屏幕阅读器才知道当前排序列与方向")
        # 排序状态每块各一份，和「标的交易明细」的每块筛选同级：两块看的是不同窗口的数据，
        # 联动会把其中一块的排序强行改掉。默认值必须仍是「关联人物降序」，首屏顺序才不会变。
        self.assertIn("const hotSort = {30: {key: 'people', direction: 'desc'}, 90: {key: 'people', direction: 'desc'}};", html)
        self.assertIn('function sortHotRows(rows, key, direction)', html)
        self.assertIn('function hotSortValue(row, key)', html)
        self.assertIn('function renderHotSortHead(days)', html)
        self.assertIn('function bindHotSort()', html)
        self.assertIn('bindHotSort();', html, msg="排序事件没在初始化时绑定，按钮点了不会动")
        # 事件绑在 <table> 上而不是按钮上：每次排序都整块替换 tbody，常驻的只有表头。
        self.assertIn('table.addEventListener(\'click\', event => {', html)
        self.assertIn("if (state.key === key) state.direction = state.direction === 'desc' ? 'asc' : 'desc';", html,
                      msg="同一列再点一次应翻转升降序")
        self.assertIn("state.direction = HOT_SORT_TEXT_KEYS.has(key) ? 'asc' : 'desc';", html,
                      msg="换列排序要重置方向：文本列升序，数值列降序")
        self.assertIn("const HOT_SORT_TEXT_KEYS = new Set(['ticker']);", html,
                      msg="代码列若默认降序，首屏会排成 Z→A")
        # 缺行情的行永远排最后：涨跌显示为「—」时它既不是涨也不是跌，放哪一端都会被读成一个结论。
        self.assertIn('if ((av === null) !== (bv === null)) return av === null ? 1 : -1;', html)
        # 名次先按默认口径定格，之后无论怎么排序，「01」始终是关注人数最多的那只。
        # 跟着当前排序重新编号的话，按涨跌降序时「01」就成了「涨幅第一」。
        self.assertIn('rows.slice().sort(hotDefaultOrder).forEach((row, index) => { row.attentionRank = index + 1; });', html,
                      msg="关注度名次必须按默认口径定格，不能跟着当前排序重新编号")
        self.assertIn('const ordered = sortHotRows(rows, hotSort[days].key, hotSort[days].direction);', html)
        # 排序按钮的三态箭头与激活态描边：激活列要有边框、白底、蓝字、方向箭头四个线索。
        self.assertIn(".hs-sort::after { content: '↕';", html)
        self.assertIn(".hs-sort.active.asc::after { content: '↑';", html)
        self.assertIn(".hs-sort.active.desc::after { content: '↓';", html)
        self.assertIn('.hs-sort.active { border-color: var(--info-edge); color: var(--info); background: #fff;', html)
        # 全局 table{min-width:850px} 是给包在横向滚动容器里的宽表用的，热门股票表要跟着面板收缩，
        # 少了 min-width:0 它会在窄屏把整页撑出横向滚动。
        self.assertIn('.hot-stock-table { width: 100%; min-width: 0; table-layout: fixed;', html,
                      msg="热门股票表缺 min-width:0，会被全局 table{min-width:850px} 撑破页面")
        self.assertIn('.hot-stock-table th:nth-child(2) { width: 250px; }', html,
                      msg="关联人物列太窄会把人物数、笔数和方向标签各折成两行")
        # 低于 1150px 就堆叠：这一列要装下「标签 + 笔数 + 金额 + 日期」四栏，再窄日期就被截断。
        # 堆叠后 thead 不隐藏——排序按钮住在里面，藏掉等于窄屏没有排序入口。
        self.assertIn('@media (max-width: 1150px) {', html)
        self.assertIn('.hot-stock-table thead tr { display: flex; flex-wrap: wrap;', html,
                      msg="窄屏 thead 应变成可换行的排序控件条，而不是被 display:none 藏掉")
        self.assertIn('.hot-stock-return-head small { display: none; }', html)
        # 空态是占满整行的 <td>，不是 div：div 放进 tbody 会被浏览器挪到表格外面。
        self.assertIn('class="hot-stock-empty" colspan="4"', html,
                      msg="空态必须是 colspan 的 <td>，否则它会被挪出表格")

    def test_priority_card_leads_with_data_not_identity(self) -> None:
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        # 卡片只写「买入 / 卖出」。选取口径（该方向全部标的，按金额上限合计降序，最多列 3 枚）
        # 由网格上方的 section-head 承载，所以那里必须写全，否则「按金额降序」这个限定会消失，
        # 读者会把卡片上的标的误读成该人窗口内随便几笔交易。
        self.assertIn('function securitiesByAmount(txs, personId, direction)', html)
        self.assertIn('function directionAggregate(txs, direction)', html)
        self.assertIn('function latestOf(txs)', html)
        card_markup = html.split('return `<article class="priority-card"', 1)[1].split('</article>`', 1)[0]
        card_source = html.split('function renderPriority(txs) {', 1)[1].split('\n}', 1)[0]
        self.assertIn('class="priority-avatar"', card_markup)
        self.assertIn('class="priority-role"', card_markup)
        self.assertIn('class="priority-kicker"', card_markup)
        self.assertIn('class="priority-id"', card_markup)
        self.assertIn('class="priority-flows">${flowMarkup}', card_markup)
        # 色块只有 20px 宽，塞不下「买入」两字，所以显示单字；散文里仍用全称。
        self.assertIn("{direction: 'purchase', short: '买', label: '买入'", html)
        self.assertIn("{direction: 'sale', short: '卖', label: '卖出'", html)
        self.assertIn('<h3>国会山重点关注</h3><p>近 30 天买卖标的</p>', html,
                      msg="section note 变了：它必须仍点明窗口长度与「买卖」，"
                          "否则卡片上的标的会被误读成随手列的几笔")
        self.assertNotIn('最多买入', html)
        self.assertNotIn('最多卖出', html)
        # 方向行由 flowMarkup 生成，断言要落在整个 renderPriority 而非按钮模板切片上。
        self.assertIn('class="priority-flow ${flow.side}"', card_source)
        self.assertIn('class="priority-flow ${flow.side} is-empty"', card_source)
        self.assertIn('class="flow-tag" aria-hidden="true"', card_source)
        self.assertIn('class="flow-head"', card_source)
        self.assertIn('class="flow-counts"', card_source)
        self.assertIn('class="flow-chips"', card_source)
        self.assertIn('class="flow-chip ${flow.side}"', card_source)
        self.assertIn('class="flow-chip more"', card_source)
        self.assertIn('class="chip-tip"', card_source)
        self.assertIn('class="flow-meta"', card_source)
        self.assertIn('class="flow-empty"', card_source)
        # 色块与「N 只 · N 笔」同处顶行：色块若独占一行，两张方向卡各多出一条 26px 的空白行。
        # .flow-body 是横带版的中间层（色块在左、内容在右），竖排后由 .priority-flow 自己承担，不该回来。
        self.assertNotIn('flow-body', html,
                         msg=".flow-body 又回来了：竖排卡片由 .priority-flow 自己当列容器，不需要这层包裹")
        # 本轮核心契约：金额不再常驻，只进悬停浮层。flow-amount 若回来，卡片就退回「一只标的 + 一行金额」，
        # 全部标的的列表会被挤掉——这正是这次要改掉的东西。
        self.assertNotIn('flow-amount', html,
                         msg="金额又直接渲染在卡片上了：标的列表会被金额挤回单只")
        # 截断只允许发生在「同时显示几枚」上，不允许让任何金额不可达：
        # 被折起来的标的必须在 +N 的浮层里逐条列出。
        self.assertIn('rest.map(item => `${item.ticker} ${money(item.low)}–${money(item.high)}`).join(', card_source,
                      msg="+N 的浮层不再列出余下标的身，被折叠的金额变得不可达")
        self.assertIn("const shown = securities.slice(0, FLOW_CHIP_LIMIT);", card_source)
        self.assertIn("const rest = securities.slice(FLOW_CHIP_LIMIT);", card_source)
        # 卡片整块可点（进人物页），aria-label 挂在姓名那个按钮上，覆盖全部子内容。
        # 色块只显示单字「买/卖」且 aria-hidden，若 aria-label 只写「查看 X 个人主页」，
        # 读屏用户就完全拿不到两个方向的标的、金额、交易日与披露日。
        self.assertIn('aria-label="查看 ${person.display_name} 个人主页：${spoken.join(', card_markup)
        self.assertIn('aria-hidden="true"', card_source)
        self.assertIn('spoken.push(`${flow.label} ${listed}', card_source)
        self.assertIn("spoken.push(`${flow.label}：${DASHBOARD_WINDOW} 天内无披露`)", card_source)
        # 卡片 aria-label 仍是「浏览模式下先听到的那一句」，所以它必须念出每一枚已渲染 chip 的
        # 代码与金额，被截断时再补总数。
        self.assertIn('const listed = shown.map(item => `${item.ticker} ${money(item.low)}–${money(item.high)}`).join(\'、\');', card_source,
                      msg="aria-label 不再逐枚口述标的与金额：读屏用户拿不到任何金额")
        self.assertIn('${rest.length ? ` 等 ${securities.length} 只` : \'\'}', card_source,
                      msg="截断未在 aria-label 里口述：只念列出的几只会丢掉余量，只念总数会让人以为卡片列全了")
        self.assertNotIn('tabindex', card_source,
                         msg="用 tabindex 当捷径了：可聚焦的位置该用真按钮，而不是往 span 上挂 tabindex")
        # 卡里现在有两个去处：姓名进人物页，每枚代码进单股页。根节点因此不能是 <button>
        # ——按钮套按钮是非法嵌套，整卡可点时 chip 就只能退回纯展示。
        self.assertIn('return `<article class="priority-card"', html,
                      msg="人物卡根节点必须是无交互语义的 <article>：卡里含有内层按钮")
        self.assertNotIn('<button class="priority-card"', html,
                         msg="人物卡又变回整块按钮了：内层按钮会构成非法嵌套")
        self.assertIn('class="priority-who" type="button" data-person-link="${person.id}"', card_markup,
                      msg="姓名不再是人物页入口：键盘用户失去了进人物页的唯一通路")
        self.assertIn('class="flow-chip ${flow.side}" type="button" data-ticker-link="${item.ticker}"', card_source,
                      msg="代码 chip 不是按钮或没挂 data-ticker-link：点了不会进单股页")
        # 浮层是鼠标专用的，读屏拿不到；chip 既然可聚焦，金额就得落在它自己的 aria-label 上。
        self.assertIn('aria-label="查看 ${item.ticker} 股票页面：${tip(item)}"', card_source,
                      msg="chip 的可访问名只有代码没有金额：键盘用户悬停不到浮层，拿不到区间")
        # 两个去处靠 capture 阶段的委托监听分派：它先跑并 stopPropagation，
        # 所以点代码不会再多跳一层人物页。改成冒泡阶段就会一次点击连跳两层。
        self.assertIn("document.querySelectorAll('.priority-card').forEach(el => el.addEventListener('click', () => openPerson(el.dataset.person)));", html,
                      msg="卡片空白处的兜底跳转没了：整卡可点退化成只有姓名和代码可点")
        self.assertIn('openPerson(personLink.dataset.personLink);\n}, true);', html,
                      msg="人物页委托监听不再跑在 capture 阶段：它会晚于卡片自己的监听，一次点击会连跳两层")
        # chip 与姓名都是真按钮，按钮默认的边框/底色必须显式清掉，否则会多出一圈系统灰边。
        chip_rule_now = html.split('.flow-chip {', 1)[1].split('}', 1)[0]
        self.assertIn('border: 0;', chip_rule_now, msg="chip 变成按钮后没清掉默认边框")
        self.assertIn('cursor: pointer;', chip_rule_now, msg="可点的 chip 应该给出 pointer")
        who_rule = html.split('.priority-who {', 1)[1].split('}', 1)[0]
        for reset in ('border: 0;', 'padding: 0;', 'background: transparent;'):
            self.assertIn(reset, who_rule, msg=f"姓名按钮没清掉默认样式：缺 {reset}")
        # +N 没有去处（它代表被折起来的几只，不是一个代码），必须留在不可点的形态上，
        # 否则读者会去点它，而它永远不会有反应。
        self.assertIn('.flow-chip.more { color: var(--muted); background: var(--panel-2); cursor: help;', html,
                      msg="+N 的 help 光标没了：它和可点的 chip 会长得一模一样")
        self.assertIn('.priority-who:focus-visible, .flow-chip:focus-visible,', html,
                      msg="新增的两个控件没进 focus-visible 规则：键盘走到它们身上看不见焦点")
        # 笔数只在合计了多笔时才补：单笔时它是噪音，多笔时不标则会被读成单笔的区间。
        self.assertIn("${item.count > 1 ? ` · ${item.count} 笔` : ''}", card_source)
        # 日期行是方向级的：一个方向现在列出多只标的，任何单只的日期都不再代表整行。
        # 「最新」限定不能省，省了就会被读成这几只都是那天买的。
        self.assertIn('`最新 ${dateLabel(agg.tradeDate)}${flow.verb}`', card_source)
        self.assertIn('`${dateLabel(agg.filedAt)}披露`', card_source)
        # 姓名/头像降级：半幅肖像整块删除，总笔数与「详情 →」不再占卡片位置。
        for gone in ('priority-photo', 'priority-count', 'priority-footer', 'priority-hint', '<i>职位</i>'):
            self.assertNotIn(gone, html, f"removed card element {gone} came back")
        self.assertIn("const role = hasDisplayValue(person.role) ? person.role : '';", html)
        self.assertIn("(role || '重点关注')", html)
        self.assertIn('hasDisplayValue(person.party) ? partyLabel(person.party) : \'\'', html)
        # 三列：卡宽约 456px。2 列时卡片是 690x141 的横条，标的与金额之间空出约 300px 死区。
        self.assertIn('.priority-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));', html)
        # 每个方向是一张竖着的圆角卡片，两张左右并排——这是本版的核心版式变更。
        # 退回 flex-direction: column 就等于退回上下两条横带，方向又变回同一根柱子的上下两段。
        self.assertIn('.priority-flows { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));', html,
                      msg="两个方向不再是左右并排：退回上下堆叠就丢掉了这次改动本身")
        self.assertIn('.priority-flow { min-width: 0; display: flex; flex-direction: column; gap: 6px;', html,
                      msg="方向卡不再是竖排内容：并排后每侧只有半个卡片的宽度，内容必须竖排")
        self.assertIn('.priority-flow.buy { border-color: var(--buy-edge); background: var(--buy-wash); }', html,
                      msg="方向卡丢了买入绿底：标的与金额又会退回白底上两块不相干的文字")
        self.assertIn('.priority-flow.sell { border-color: var(--sell-edge); background: var(--sell-wash); }', html,
                      msg="方向卡丢了卖出红底：标的与金额又会退回白底上两块不相干的文字")
        # 空方向保留整张卡片与色块，只换成中性配色：骨架不塌，色块仍说明空的是哪个方向。
        self.assertIn('.priority-flow.is-empty { border-color: var(--line); background: var(--panel-2); }', html,
                      msg="空方向又带上了方向底色：空态会读成「确实有这笔交易」")
        # 并排的等宽靠 grid 的 minmax(0, 1fr)：少了 minmax 的 0 下限，
        # 内容更宽的一侧会撑破轨道，两张卡就不等宽了。
        flows_rule = html.split('.priority-flows {', 1)[1].split('}', 1)[0]
        self.assertIn('align-items: stretch', flows_rule,
                      msg="两个方向卡不再等高：一侧没有披露时另一侧会塌成半张卡")
        # 方向靠实心色块承载，不再用浅底深字；色块必须 aria-hidden，否则「买」这个单字
        # 会和 aria-label 里的「买入」重复念一遍。
        self.assertIn('display: grid; place-items: center; width: 20px; height: 20px; border-radius: 5px;', html,
                      msg=".flow-tag 的几何被改动了：色块尺寸是卡片顶行宽度的基准，改了要一并重算断点")
        self.assertIn('.priority-flow.buy .flow-tag { background: var(--buy); }', html,
                      msg="买入色块退回浅底深字：方向在白卡上几乎看不出是一块颜色")
        self.assertIn('.priority-flow.sell .flow-tag { background: var(--sell); }', html,
                      msg="卖出色块退回浅底深字：方向在白卡上几乎看不出是一块颜色")
        # 标的排成可折行的一行 chip；金额移入浮层后 chip 本身就是这一行唯一的宽度来源。
        chips_rule = html.split('.flow-chips {', 1)[1].split('}', 1)[0]
        self.assertIn('.flow-chips { position: relative;', html)
        self.assertIn('flex-wrap: wrap', chips_rule, msg="chip 行不再折行：窄列里标的会被压出卡片")
        chip_rule = html.split('.flow-chip {', 1)[1].split('}', 1)[0]
        self.assertIn('cursor: pointer', chip_rule,
                      msg="可点的 chip 丢了 pointer 光标：读者看不出代码是可以点进单股页的")
        # 浮层挂在这一行上而不是逐枚 chip 上：并排后每列只有半个卡片宽，锚在 chip 上左对齐，
        # 浮层必然冲出这一列、压住旁边那张方向卡的计数行。chip 一旦又是 relative，就退回去了。
        self.assertNotIn('position: relative', chip_rule,
                         msg="chip 又变成定位元素：浮层会重新挂回 chip，max-width 的列宽上限随之失效，"
                             "金额浮层会压到相邻方向卡上")
        # 浮层几何。left: 0 对齐整行 chip 的左缘；max-width: 100% 把上限钉死在列宽上，
        # 这两条合起来保证浮层永不越出本列。width: fit-content 让短浮层仍贴合内容。
        tip_rule = html.split('.chip-tip {', 1)[1].split('}', 1)[0]
        self.assertIn('left: 0', tip_rule, msg="浮层不再是左对齐：会顶出列的右缘")
        self.assertIn('bottom: calc(100% + 6px)', tip_rule, msg="浮层不再浮在 chip 行上方")
        self.assertIn('max-width: 100%', tip_rule,
                      msg="浮层丢了列宽上限：长金额会带着深色底冲出本列，变成一段读不出背景的裸字")
        self.assertIn('width: fit-content', tip_rule,
                      msg="浮层不再贴合内容：所有金额都会撑成一条占满列宽的深色条")
        self.assertIn('overflow-wrap: anywhere', tip_rule,
                      msg="浮层丢了长段兜底：金额区间中间没有可断点，窄列里会溢出深色底")
        self.assertIn('visibility: hidden', tip_rule,
                      msg="浮层藏起来时只用了 opacity：它仍占位并可能被读到，必须同时 visibility: hidden")
        self.assertIn('pointer-events: none', tip_rule,
                      msg="浮层可接收指针事件：它会挡住自己的 chip，悬停会在浮层与 chip 之间抖动")
        self.assertIn('.flow-chip:hover .chip-tip { opacity: 1; visibility: visible; }', html,
                      msg="浮层的显示规则没了：悬停不再展示金额，而金额现在是唯一通路")
        # render() 每次 innerHTML 重建整个网格，绑在 chip 上的事件会被重渲抹掉，所以浮层必须纯 CSS。
        # 这条断言同时锁住「不要改成 JS tooltip」这个约束，并锁住 .flow-chips 的定位上下文
        # （上面已断言 chip 自己不是定位元素，两处合起来才钉死浮层挂在哪一级）。
        self.assertIn('.flow-chip:hover .chip-tip { opacity: 1; visibility: visible; }', html,
                      msg="浮层的显示规则没了：悬停不再展示金额，而金额现在是唯一通路")
        self.assertIn('.priority-card {', html)
        self.assertIn('.priority-card:hover { z-index: 2;', html,
                      msg="卡片 :hover 的 z-index: 2 被删了：抬起 2px 时会压不过下面的邻居")
        # 交易日与披露日各占一行，不再用「·」串成一句：竖排卡片里串成一行会被挤到折行，
        # 两段之间的边界反而消失。每一段自己 nowrap，所以任何一段都不会被从中间截断。
        meta_rule = html.split('.flow-meta {', 1)[1].split('}', 1)[0]
        self.assertNotIn('ellipsis', meta_rule, f".flow-meta 又用上省略号了：{meta_rule}")
        self.assertNotIn('nowrap', meta_rule, f".flow-meta 又禁止整列折行了：{meta_rule}")
        self.assertIn('flex-direction: column', meta_rule,
                      msg="两个日期又并回一行了：竖排卡片里它们会被挤到折行，边界随之消失")
        self.assertIn('.flow-meta > span { white-space: nowrap; }', html,
                      msg="日期分段不再是 nowrap：交易日或披露日会被从中间截断")
        # 「·」只留给顶行的「N 只 · N 笔」。日期列上不能再补分隔符，
        # 竖排之后那会让第二行以一个孤立的「·」开头，读起来像一段被截断的残句。
        self.assertIn('.flow-counts > span + span::before { content: "·";', html,
                      msg="只数与笔数之间的「·」没了：两个计数会糊成一段")
        self.assertNotIn('.flow-meta > span + span::before', html,
                         msg="日期列又补了「·」：竖排后第二行会以一个孤立的分隔符开头")
        # 每方向一行所需宽度 = 色块 20 + 间距 9 + 标的 + 间距 10 + 金额（六位数区间约 190px）。
        # 三列下 1000px 视口只剩 172px 给这一行，金额放不下，所以 1000px 退两列；
        # 两列下 620px 视口卡片 280px，再窄金额贴边，所以 620px 退单列。
        self.assertIn('@media (max-width: 1000px) {', html)
        self.assertIn('@media (max-width: 620px) {', html)

    def test_buy_consensus_lists_top_buy_stocks(self) -> None:
        # 「国会山买入共识」：窗口内买入披露金额上限合计最高的标的，每只带买入金额最高的前三位政客。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)

        self.assertIn('function buyConsensus(txs, limit)', html)
        self.assertIn('function renderBuyConsensus(days)', html)
        # 两块同时渲染，与下方表格区块一致——不是靠 30/90 按钮切换的一块。
        self.assertEqual(html.count('data-consensus-window="30"'), 1, "30 天共识块缺失或多于一块")
        self.assertEqual(html.count('data-consensus-window="90"'), 1, "90 天共识块缺失或多于一块")
        self.assertIn('id="consensusGrid30"', html)
        self.assertIn('id="consensusGrid90"', html)
        self.assertIn('renderBuyConsensus(days);', html, "共识块未接进 renderDashboardBlocks 的 forEach")
        self.assertIn("const CONSENSUS_LIMIT = 6;", html)
        self.assertIn('.slice(0, limit)', html)

        # 顶部 30/90 锚点靠 querySelector 取首个匹配，共识块一旦带上 data-window-block
        # 就会把锚点劫持到共识块而不是表格。锚点契约本轮不变。
        self.assertEqual(html.count('data-window-block="30"'), 1,
                         "共识块复用了 data-window-block：顶部 30/90 锚点会跳到共识块而不是表格")
        self.assertIn('data-consensus-window="30"', html,
                      msg="共识块应改用 data-consensus-window 与锚点块区分开")

        card_source = html.split('function renderBuyConsensus(days) {', 1)[1].split('\n}', 1)[0]
        # 卡片根节点是 <article> 而不是整块 <button>：卡内有「单股页 →」链接按钮，
        # 按钮嵌套按钮是非法 HTML，点击语义也会打架。
        self.assertIn('class="consensus-card"', card_source)
        self.assertNotIn('<button class="consensus-card"', html,
                         msg="共识卡变成了整块按钮：卡内还有链接按钮，按钮嵌套按钮是非法嵌套")
        self.assertIn('data-ticker-link="${row.ticker}"', card_source,
                      msg="共识卡的单股页链接丢了 data-ticker-link：全局委托监听接不到它")
        # 整张卡进单股页，但人物行要能停下来进人物页——两个去处各有一个目标元素。
        # 卡根挂的是 data-consensus-card 而不是 data-ticker-link：后者那条委托监听跑在 capture 阶段
        # 且会 stopPropagation，挂在卡根上会把人物行上的 data-person-link 一并拦走，人物页就再也进不去。
        self.assertIn('class="consensus-card" data-consensus-card="${row.ticker}"', card_source,
                      msg="共识卡根节点没有可跳单股页的标记")
        self.assertNotIn('class="consensus-card" data-ticker-link', card_source,
                         msg="共识卡根节点用了 data-ticker-link：capture 阶段的委托监听会连人物行一起吃掉")
        self.assertIn("document.querySelectorAll(`#consensusGrid${days} .consensus-card`).forEach(el => el.addEventListener('click', () => goTicker(el.dataset.consensusCard)));", html,
                      msg="共识卡整块可点的监听没了：只有「单股页 →」那颗按钮能进单股页")
        self.assertIn('class="consensus-person" type="button" data-person-link="${person.id}"', card_source,
                      msg="共识卡的头像/姓名不是人物页入口了")
        # 两个去处共用同一个跳转入口：人物页开着时按「从人物页进单股页」走，否则直接从看板进。
        # 各写一份的话，返回栈的行为迟早会分叉。
        self.assertIn('goTicker(tickerLink.dataset.tickerLink);', html,
                      msg="委托监听没走共用的 goTicker：两条进入单股页的路径会分叉")
        # 人物行的 span 从 li 的直接子级挪进了按钮里，选择器不跟着改就会静默丢版式
        # （姓名不再省略、职位与笔数挤成一行）。
        self.assertIn('.consensus-person > span {', html,
                      msg=".consensus-people li > span 没有跟着结构改成 .consensus-person > span："
                          "姓名与职位行会丢掉竖排与省略号")
        self.assertIn('.consensus-person:hover img { box-shadow: 0 0 0 2px var(--signal); }', html,
                      msg="人物行悬停没有提示：读者看不出头像是可以点进人物页的")
        self.assertIn('.consensus-card:hover { z-index: 2; transform: translateY(-2px);', html,
                      msg="整卡可点却没有任何悬停反馈：读者只会当它是一块静态版面")
        # 买入合计是上下限分别求和，不是净额。
        self.assertIn('买入合计 ${money(row.agg.low)}–${money(row.agg.high)}', card_source)
        self.assertNotIn('净流入', html)
        self.assertNotIn('净流出', html)
        # 不设买家数门槛：一只股票只要买入金额最大就入选，哪怕只有一个买家。
        # 门槛藏在聚合里会让「为什么这只没出现」变得无法从界面推断。
        self.assertIn("txs.forEach(t => {\n    if (t.transaction_type !== 'purchase' || !t.ticker) return;", html,
                      msg="买入共识不再只按买入记录分组")
        self.assertNotIn('buyerCount >= ', html, msg="买入共识被加上了买家数门槛")
        self.assertNotIn('至少 2', html)
        # 涨跌幅缺值不着色：缺值既不是涨也不是跌。
        self.assertIn("${hasReturn ? returnClass(returnValue) : ''}", card_source,
                      msg="共识卡涨跌幅未按缺值降级：没有行情时会被染成涨或跌")
        self.assertIn("暂无可比窗口行情", card_source)
        # 交易日与披露日必须始终可区分。
        self.assertIn('最新 ${dateLabel(row.agg.tradeDate)}买入', card_source)
        self.assertIn('${dateLabel(row.agg.filedAt)}披露', card_source)
        # 断点与人物卡网格对齐：3 → 2 → 1。
        self.assertIn('.consensus-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));', html)
        consensus_rule = html.split('.consensus-return {', 1)[1].split('}', 1)[0]
        self.assertNotIn('color', consensus_rule,
                         msg=".consensus-return 自己设了 color：它的源序在 .positive/.negative 之后，"
                             "会压过涨跌配色，涨跌两边同色")

    def test_hot_stock_rows_carry_split_direction_amounts_and_dates(self) -> None:
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        self.assertIn('class="hot-stock-flow-row ${side}"', html)
        self.assertIn('class="hs-side"', html)
        self.assertIn('class="hs-figures"', html)
        self.assertIn('class="hs-amount"', html)
        self.assertIn('class="hs-dates"', html)
        self.assertIn('class="hs-empty"', html)
        # 分方向金额是上下限分别求和，不是净额；日期必须区分交易日与申报日。
        self.assertIn("${dateLabel(agg.tradeDate)}${verb} · ${dateLabel(agg.filedAt)}", html)
        self.assertNotIn('净流入', html)
        self.assertNotIn('净流出', html)
        self.assertIn('flowRow(\'买入\', \'buy\', directionAggregate(related, \'purchase\'))', html)
        self.assertIn('flowRow(\'卖出\', \'sell\', directionAggregate(related, \'sale\'))', html)

    def test_stock_disclosure_table_uses_pill_direction_chips(self) -> None:
        # 单股页「政客交易明细」照参考看板改成胶囊方向标 + 圆形头像 + 右对齐的数字列。
        result = build_snapshot(production_payload())
        html = render_html(result)

        # 三处行模板（当前窗口明细、冻结参考聚合、冻结参考明细）都要带这三个单元格类；
        # 少一处，那一类行就会退回旧的内联样式，和同表其它行长得不一样。
        self.assertIn('class="sd-direction"', html, "交易明细行缺 sd-direction 容器：方向胶囊与小字副行会失去布局")
        self.assertEqual(
            html.count('class="sd-direction"'),
            3,
            "sd-direction 应出现在全部三处行模板（当前窗口 / 参考聚合 / 参考明细）",
        )
        self.assertIn('.stock-disclosure-table .sd-pills { display: inline-flex; gap: 6px; }', html, "参考聚合行并排两枚胶囊缺样式")
        self.assertIn('.stock-disclosure-table tbody tr { height: 64px; }', html, "交易明细行高下限缺失：有无「延迟 N 天」小字的行会一高一矮")

        # 胶囊本身：全圆角 + 语义底色。底色取 --buy-wash/--sell-wash，与表格上方那条买卖结构条同源，
        # 否则同一个方向在同一页会出现两种绿。
        self.assertIn(
            '.stock-disclosure-table .trade-type { min-width: 42px; justify-content: center; padding: 7px 15px; border-radius: 999px; font: 600 12px/1.2 var(--sans); letter-spacing: 0; text-transform: none; }',
            html,
            "方向胶囊样式缺失或退化：会退回全局 mono 大写小字的方向标记",
        )
        self.assertIn(
            '.stock-disclosure-table .trade-type.buy, .stock-disclosure-table .trade-type.purchase { color: var(--buy); background: var(--buy-wash); }',
            html,
            "买入胶囊配色缺失（参考聚合行用的是 .purchase，漏掉它那两枚胶囊会不着色）",
        )
        self.assertIn(
            '.stock-disclosure-table .trade-type.sell, .stock-disclosure-table .trade-type.sale { color: var(--sell); background: var(--sell-wash); }',
            html,
            "卖出胶囊配色缺失（参考聚合行用的是 .sale，漏掉它那两枚胶囊会不着色）",
        )
        # 胶囊底色已经交代方向，同色圆点是同一件事说两遍。
        self.assertIn('.stock-disclosure-table .trade-type::before { display: none; }', html, "胶囊内残留方向圆点")
        # 但圆点必须给人物页那张 8 列宽表留着——那里没有胶囊，去掉圆点就没有方向编码了。
        self.assertIn(
            '.trade-type::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }',
            html,
            "人物页交易表的 .trade-type 方向圆点被误删",
        )

        # 圆形头像这条规则和上方 .stock-disclosure-table 块里的同权重，靠源码顺序决胜；
        # 一旦被挪到前面就会退回 4px 圆角方块，所以连尺寸一起锁住。
        self.assertIn(
            '.stock-table .stock-person img { width: 36px; height: 36px; border-radius: 50%;',
            html,
            "交易明细头像不是 36px 圆形：该规则与 .stock-disclosure-table 块同权重，被移到前面就会被压掉",
        )

        # 金额与两个日期右对齐（含表头）：同列数字按位对齐才比得出大小。
        self.assertIn(
            '.stock-disclosure-table th:nth-child(n+3), .stock-disclosure-table td:nth-child(n+3) { text-align: right; }',
            html,
            "金额与日期列未右对齐",
        )
        # 金额格直接调 range()，保持官方披露档位的精确写法（$1,001–$15,000）。
        # 参考看板用的是 $1k–$15k 这类压缩写法，但那会把下限读数rounding到 $1,000，与本看板
        # 「金额是申报档位、不是估算成交额」的规则冲突，故只借样式不借这个格式。
        self.assertIn('<td class="sd-amount">${range(t)}</td>', html, "金额格不再走 range()：精确披露档位可能被换成压缩写法")
        self.assertIn('const range = t => `${money(t.amount_low)}–${money(t.amount_high)}`;', html, "range() 被改写，金额不再是上下限原值")
        # 参考看板那枚「反向」标在本看板没有对应字段，不得凭空造一个方向判断。
        self.assertNotIn('反向', html, "出现了没有数据来源的「反向」标：本看板没有该字段，属推断信号")

    def test_person_page_is_one_transaction_detail_view(self) -> None:
        # 人物页收成一页：画像 / 交易 / 披露后表现三条页签合并为「交易明细」。
        # 这一条同时钉住两件事——被删掉的块不能再回来，搬过来的块不能半路丢掉。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)

        # 页签机制整体退场：标记、状态变量、监听、样式四处都要清零，漏一处就是半死不活的控件。
        self.assertNotIn('data-person-tab=', html, "人物页仍有页签按钮")
        self.assertNotIn('personTab', html, "personTab 状态变量残留：页签已并入单页")
        self.assertNotIn('class="person-tabs"', html, "人物页页签容器还在")
        self.assertNotIn('.person-tabs {', html, "人物页页签样式还在")

        # 集合数据与买卖结构：整块删除，标记与样式一起走。
        self.assertNotIn('class="person-metrics"', html, "人物页集合数据条还在")
        self.assertNotIn('.person-metrics {', html, "人物页集合数据样式还在")
        self.assertNotIn('class="person-direction"', html, "人物页买卖结构条还在")
        self.assertNotIn('.person-direction {', html, "人物页买卖结构样式还在")

        # 申报持仓 / 年度披露量 / 最常交易股票 / 人物画像：四张卡连同样式与点击目标一起删除。
        # 断言的 <h3> 字面量取自冻结的 v2 产物，逐字对得上，不是猜的。
        # reported_holdings 仍留在数据里（单股页拿它兜底公司名），但不再有人物页入口。
        self.assertNotIn('class="person-holdings"', html, "人物页申报持仓卡还在")
        self.assertNotIn('<h3>申报持仓</h3>', html, "人物页仍渲染申报持仓卡")
        self.assertNotIn('<h3>年度披露量</h3>', html, "人物页仍渲染年度披露量卡")
        self.assertNotIn('<h3>最常交易的股票</h3>', html, "人物页仍渲染最常交易股票卡")
        self.assertNotIn('data-person-ticker=', html, "最常交易股票的点击目标还在")
        self.assertNotIn('person-ticker-', html, "最常交易股票的样式残留")
        self.assertNotIn('data-person-focus=', html, "买卖结构条上的方向跳转按钮还在")
        self.assertNotIn('<h3>人物画像</h3>', html,
                         "「人物画像」总述卡仍在：内容与人物页摘要重复，且与「交易明细」的页名冲突")

        # 保留下来并搬进来的部分。
        self.assertIn('<h3>交易明细披露</h3>', html, "交易披露卡没改名成「交易明细披露」")
        self.assertIn('<h3>披露后表现观察</h3>', html, "披露后表现观察没跟着搬进交易明细页")
        # 披露后表现提到交易明细披露之上：两个 <section> 在 .person-main 里的先后就是源码顺序，
        # 断言整段前缀，光查两者都存在是查不出顺序的。
        self.assertIn('<main class="person-main">${performanceBody}<section class="person-card">', html,
                      "披露后表现没有排在交易明细披露之前")
        self.assertIn('.person-main .performance-observation { margin-top: 0; }',
                      html, "披露后表现自带 margin-top 未清零：会和 .person-main 的 gap 叠成 36px")
        self.assertIn('<h3>所有人归属</h3>', html, "所有人归属卡被误删")

        # 大额披露样本板块整体退场：标记、数据、样式三处一起删。
        # 它按「大额优先」重排 performanceSample，与下方明细表同源，逐笔唯一多出来的字段
        # 是「披露至今该股涨跌」，而那只在整块样本上取中位数时才成立。
        self.assertNotIn('class="performance-detail"', html, "大额披露样本板块还在")
        self.assertNotIn('.performance-detail {', html, "大额披露样本板块样式还在")
        self.assertNotIn('<h3>大额披露样本</h3>', html, "人物页仍渲染大额披露样本卡")
        self.assertNotIn('<h3>大额 / 期权披露明细</h3>', html, "人物页仍渲染大额 / 期权披露明细卡")
        self.assertNotIn('class="performance-card-grid"', html, "大额披露样本卡片网格还在")
        self.assertNotIn('performance-disclosure-card', html, "大额披露样本卡片样式残留")
        self.assertNotIn('performanceDetailTxs', html, "大额披露样本的取样变量残留")
        self.assertNotIn('[data-person-performance-tx]', html, "大额披露样本卡片的点击监听残留")
        self.assertNotIn('data-person-performance-tx=', html, "大额披露样本卡片的点击目标还在")
        self.assertNotIn('.performance-amount', html, "大额披露样本的金额样式残留")
        # underlying_return_since_filing 没有被一起删掉：表现观察的三格聚合仍要吃它。
        self.assertIn('Number(t.underlying_return_since_filing)', html,
                      "披露至今涨跌字段被连带删除：表现观察的吻合率与中位数会算不出来")

        # 新列：交易日至今涨跌幅。插在交易日与申报日之间——两端算的是不同的日子，必须能分辨。
        self.assertIn('<th>交易日至今涨跌幅</th>', html, "交易明细表没有「交易日至今涨跌幅」列")
        self.assertIn('<table class="person-table"><thead><tr><th>标的</th><th>所有人</th><th>方向</th><th>金额</th>'
                      '<th>交易日</th><th>交易日至今涨跌幅</th><th>申报日</th><th>滞后</th><th>交易类型</th></tr></thead>',
                      html, "「交易日至今涨跌幅」没有插在交易日与申报日之间")
        self.assertIn('<td class="table-return">', html, "「交易日至今」单元格缺 class，涨跌色无法按列收窄")
        self.assertIn('.person-table { min-width: 760px; }', html,
                      "人物页表格下限被抬高：抬高下限只会在桌面上逼出无谓的内部横向滚动")
        self.assertNotIn('min-width: 1030px', html, "人物页表格仍按列数拍了一个 1030px 的下限")

    def test_person_aside_is_narrow_and_headers_stay_on_one_line(self) -> None:
        # 表头不折行的几何是三段拼出来的，缺一段就退回折行。断言分三组：
        #   1) 右栏固定 250px（把宽度让给 9 列表格）；
        #   2) .person-layout 在 1280px 塌成单栏（两栏在 1280 以下给不出 ~952px 的列宽）；
        #   3) 两档内边距（9 列 × 2 边 = 18 条边，每收 1px 省 18px），把不折行下限从
        #      1012px 视口压到 904px 视口。
        # 实测（headless Chromium，逐行量各列 max(表头,内容)+内边距 得到 ~952px；折行与否用
        # Range 数行盒判定）：1920→904px 视口全程不折行，903px 起折「所有人」「交易日至今涨跌幅」。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)

        self.assertIn('.person-layout { display: grid; grid-template-columns: minmax(0,1fr) 250px;'
                      ' gap: 18px; margin-top: 18px; align-items: start; }', html,
                      "右栏不是固定 250px：比例宽度在 1050–1240px 一段只剩 673–940px 给主栏，"
                      "9 列表格在那里必然折行")
        self.assertNotIn('grid-template-columns: minmax(0,1.45fr) minmax(310px,.55fr)', html,
                         "右栏退回了比例宽度：固定宽度是这里的下限约束，不是风格选择")
        self.assertIn('@media (max-width: 1280px) {\n    .person-layout { grid-template-columns: 1fr; }',
                      html,
                      ".person-layout 没有在 1280px 塌成单栏：两栏布局在 1280px 以下给不出 ~952px 列宽")
        self.assertIn('.person-table th, .person-table td { padding-left: 12px; padding-right: 12px; }', html,
                      "缺第一档内边距：不折行下限退回 1012px 视口，960/1000/1024 这些半屏窗口会折行")
        self.assertIn('.person-table th, .person-table td { padding-left: 10px; padding-right: 10px; }', html,
                      "缺第二档内边距：不折行下限停在 940px 视口")
        # 反面：靠 white-space: nowrap 让表头一行放下是**被否决**的做法——它会在
        # 1150px 上下逼出约 70px 的内部横向滚动，等于把刚去掉的问题请回来。
        self.assertNotIn('.person-table { min-width: 760px; white-space: nowrap', html,
                         "表头改用 nowrap 硬撑：会在桌面上逼出内部横向滚动")
        self.assertNotIn('.person-security small { max-width: 90px', html,
                         "改用截断公司名来省宽度：那会真的藏掉文字（实测 110px 上限就截断 2 个公司名），"
                         "收内边距一个字节都不藏")

    def test_direction_bars_are_pills_with_a_white_gap(self) -> None:
        # 红绿条按参考图改成胶囊：整条是圆角轨道，绿段红段各自全圆角，中间留一道白间隙。
        # 两色直接相接时交界处会被读成一个渐变，白间隙才让「两块」读得出来——所以轨道底色是
        # 白（不是原来的灰），间隙露出的是白。
        result = build_snapshot(production_payload())
        html = render_html(result)

        self.assertIn('.split-bar { display: flex; width: 100%; height: 12px; padding: 2px; gap: 3px;'
                      ' border-radius: 999px; background: #fff;'
                      ' box-shadow: inset 0 0 0 1px var(--line); }', html,
                      "红绿条不是参考图的胶囊版式（整条圆角轨道 + 白底 + 白间隙）")
        self.assertIn('.bar-segment { min-width: 4px; height: 100%; padding: 0; border: 0;'
                      ' border-radius: 999px;', html,
                      "色段没有各自全圆角：两段会拼成一条无间隙的横条")
        self.assertIn('gap: 3px', html, "色段之间没有留白：两色相接会被读成渐变")
        # overflow: hidden 是**故意不加**的：段本身已全圆角又内缩 2px，不需要裁剪；
        # 加了会把 hover 的 scaleY(1.25)（8→10px）和 :focus-visible 的 outline-offset: 3px
        # 一起裁掉——焦点环本就应该画在轨道外面，裁掉等于键盘用户看不到焦点。
        self.assertNotIn('.split-bar { display: flex; width: 100%; height: 12px; padding: 2px; gap: 3px;'
                         ' border-radius: 999px; background: #fff; overflow: hidden', html,
                         "轨道加了 overflow: hidden：会裁掉 hover 的 scaleY 与键盘焦点环")
        self.assertIn('.bar-segment:hover { filter: brightness(1.22); transform: scaleY(1.25); }', html,
                      "色段的 hover 反馈不见了：它是「这条可以点」的唯一视觉提示")

    def test_price_chart_marks_trades_with_portraits_and_shows_close_on_hover(self) -> None:
        # 价格走势上按人头像标出买卖点，悬停看该点股价。三条契约必须同时成立：
        #   1) 标记落在**交易日**上，不是成交日价——披露范围内没有成交价；
        #   2) 标记处显示的股价是**该日收盘价**，措辞必须把两者分开，否则一张「头像 + 价格」
        #      的图很容易被读成这笔交易的成交价；
        #   3) 非买卖点也要能悬停看股价，所以命中区是一整条 plot rect，不是只有标记。
        result = build_snapshot(production_payload())
        html = render_html(result)

        self.assertIn('function nearestSeriesIndex(series, date)', html,
                      "缺交易日→序列下标的就近映射：头像会落不到正确的位置")
        self.assertIn('function priceMarkSlots(series, txs, xOf, yOf, top, plotHeight)', html,
                      "缺标记排布函数：同一交易日的多笔会互相压叠成一个点")
        self.assertIn('const priceChartSvg = priceChart(market, allTickerTxs);', html,
                      "价格走势没有接到交易明细：图上不会出现任何标记")
        self.assertIn('clip-path="url(#priceMarkClip-', html,
                      "头像没有按圆形裁剪：方角人像会露出一圈底色")
        self.assertIn('<clipPath id="priceMarkClip-${market.ticker}"><circle r="9"></circle></clipPath>', html,
                      "缺头像的圆形裁剪定义")
        self.assertIn('data-price-mark=', html, "标记缺 data-price-mark，悬停取不到对应记录")
        self.assertIn('<rect class="price-hit" x="${left}" y="${top}" width="${plotWidth}"'
                      ' height="${plotHeight}"></rect>', html,
                      "缺整条 plot 的命中区：非买卖点无法悬停看股价")
        self.assertIn('let priceChartGeo = null;', html,
                      "缺图表几何的持有变量：悬停时算不出光标落在哪个交易日")
        self.assertIn('.price-tip { position: fixed;', html, "悬浮卡样式不见了")

        # 措辞：图上标的是交易日 + 该日收盘价，不是成交价。这两句是诚实性的唯一通路。
        self.assertIn('标记显示的是该日<b>收盘价，不是这笔交易的成交价</b>。',
                      html, "图下缺少「收盘价不是成交价」的说明：这张图会被读成成交价")
        self.assertIn('收盘价不是成交价', html, "标记悬浮卡内缺少同样的限定")
        self.assertIn('头像标在披露人的<b>交易日</b>上，买在上、卖在下', html,
                      "缺「标记落在交易日上、买上卖下」的说明")
        self.assertIn('头像标出 ${markLayout.buyPeople} 位买入', html,
                      "SVG aria-label 没有口述头像标记代表什么：悬停是纯鼠标通路，读屏用户拿不到")
        self.assertIn('标记处为当日收盘价', html,
                      "SVG aria-label 没有口述标记处的价格口径，读屏用户会把收盘价读成成交价")
        # 上限 4 行、超出折叠成 +N：折叠必须把每一笔都在悬浮卡里列全，否则有记录变得不可达。
        # 行距 28 不是随手取的：标记外径（含描边）是 24，行距必须大于它才留得出缝——22 时外径
        # 正好等于行距，相邻两枚的圈相切，整个栈看起来是叠在一起的。
        self.assertIn("const R = 10, PITCH = 28, MAX_ROWS = 4;", html,
                      "标记的行距被改回小于外径的值：同行相邻的圈会相切，密集交易日读不出个数")
        self.assertIn('另有 ${mark.folded.length} 笔未展开', html,
                      "+N 折叠卡没有列出被折叠的每一笔：有记录会变得不可达")
        # 方向分栏必须是硬保证：同一方向的标记共用一个基线（不是各自从自己的曲线点起算），
        # 否则一个卖点高于某个买点时，往下排的卖标记会落进往上排的买标记里，两色圈交叉。
        self.assertIn('const base = ys.length ? (kind === \'buy\' ? Math.min(...ys) : Math.max(...ys)) : 0;', html,
                      "标记改回各自从自己的曲线点起算：买卖两种颜色的圈会在价格交错时互相穿插")
        self.assertIn('if (sellTop < buyBottom + GAP) sellSlots.forEach(slot => { slot.cy += buyBottom + GAP - sellTop; });',
                      html,
                      "缺买块/卖块之间的净空修正：两个方向仍可能贴在一起")
        # 引线先于全部头像绘制。合并成一趟时，卖块的长引线会横穿买块头像并印在脸上。
        self.assertIn('const markStems = markLayout.slots.map(slot => {', html,
                      "引线没有独立成一趟：后画头像的引线会压在先画头像之上")
        self.assertIn('const markSvg = markStems + markLayout.slots.map(slot => {', html,
                      "引线没有排在任何头像之前：交叉的虚线会画在头像上面")
        self.assertIn('.stock-price-chart .price-mark-stem.buy { stroke: var(--buy); }', html,
                      "引线的方向色仍用后代选择器：引线已不在 .price-mark 内，两色都会丢")

    def test_price_chart_hover_cursor_tracks_the_pointer(self) -> None:
        # 悬停读价时，绿点 + 竖直虚线要跟着鼠标走。它是图上**唯一**的绿色指示：原来那条静态的
        # 上季末基准线已按评审意见删除——两条一模一样的绿虚线并排时，读不出哪条是活的。
        # 基准价本身没有丢：图下说明写着日期和收盘价，现价格写着「以上季末为基准」，都是文字。
        # 三条契约：
        #   1) 静态基准线不得回来（回来就会重新出现两条同款绿虚线）；
        #   2) 绿点吸附到**最近的日线点**（不随鼠标连续滑动），且与曲线共用同一套 x/y 换算——点必须
        #      落在曲线上、且与悬浮卡报出的收盘价是同一个点，否则同一屏里点、线、数字三者对不上；
        #   3) 游标不吃指针（pointer-events: none），否则命中会判给游标、下面的 .price-hit 直接失效。
        result = build_snapshot(production_payload())
        html = render_html(result)

        # 1) 静态基准线已删除，且删除后基准价仍然可达（说明与现价格里写着日期和价格）
        self.assertIn('<g class="price-cursor"><line class="price-cursor-line" y1="${top}"'
                      ' y2="${top+plotHeight}"></line><circle class="price-cursor-dot" r="5"></circle></g>', html,
                      "缺游标元素：绿点与竖虚线不会跟着鼠标动")
        self.assertNotIn('class="quarter-dot"', html,
                         "静态上季末基准点回来了：它和鼠标游标是两条同款绿虚线，并排时读不出哪条是活的")
        self.assertNotIn('class="quarter-line"', html,
                         "静态上季末基准线回来了：它和鼠标游标是两条同款绿虚线，并排时读不出哪条是活的")
        self.assertNotIn('const referenceIndex =', html,
                         "上季末基准下标又算了：只会再喂给一条静态基准线，属于死代码")
        # 删掉曲线上的基准标记后，图下说明是基准价的唯一出处：日期与价格必须都还在文字里，
        # 否则这笔基准价对读屏用户、以及任何不悬停的人就整条不可达了。
        self.assertIn("上季末（${market?.previous_quarter_end || '—'}）收盘约 <b>"
                      "${market ? priceMoney(market.previous_quarter_end_price) : '—'}</b>", html,
                      "删掉曲线上的基准标记后，说明里的日期或价格也没了：基准价变得不可达")
        self.assertIn('是本季涨跌的基准价，不是政客成交价', html,
                      "删掉曲线上的基准标记后，图下说明是基准价的唯一出处，不能一起删")
        self.assertIn("const group = svg.querySelector('.price-cursor');", html,
                      "游标节点没有被取用：绿点与竖虚线不会跟着鼠标动")

        # 2) 吸附到最近的日线点，x/y 与曲线同源
        self.assertIn('function setPriceCursor(svg, geo, index) {', html, "缺游标定位函数")
        self.assertIn('priceChartGeo = {series, left, plotWidth, top, plotHeight, min, max, marks:', html,
                      "geo 没有带上刻度参数：游标要自己换算 x/y，会另写一份与曲线不同的算法")
        self.assertIn('const px = (geo.left + index / (geo.series.length - 1) * geo.plotWidth).toFixed(2);', html,
                      "游标的 x 与曲线不是同一套换算：绿点会漂离曲线")
        self.assertIn('const py = (geo.top + (geo.max - geo.series[index].close)'
                      ' / Math.max(geo.max - geo.min, 1) * geo.plotHeight).toFixed(2);', html,
                      "游标的 y 与曲线不是同一套换算：绿点会浮在曲线之外")
        self.assertIn('cursor.line.setAttribute(\'x1\', px);', html, "竖虚线没有跟着绿点走")
        self.assertIn('cursor.dot.setAttribute(\'cy\', py);', html, "绿点没有落在曲线的高度上")
        # 悬停头像时游标也吸附：头像的 x 就是它交易日的 x，两者落在同一条竖线上。
        self.assertIn('setPriceCursor(svg, geo, (inside || markEl) ? index : null);', html,
                      "游标没有跟随指针（或悬停头像时不吸附）：点会停在上一次的位置")
        self.assertIn('if (index === null) { cursor.group.classList.remove(\'is-on\'); return; }', html,
                      "游标没有离开绘图区就隐藏的通路：指针移开后绿点会留在图上")

        # 3) 样式与不吃指针
        self.assertIn('.stock-price-chart .price-cursor { visibility: hidden; pointer-events: none; }', html,
                      "游标缺 pointer-events: none：它会吃掉指针，非买卖点的悬停读数整条失效")
        self.assertIn('.stock-price-chart .price-cursor.is-on { visibility: visible; }', html,
                      "游标缺显示开关")
        self.assertIn('.stock-price-chart .price-cursor-line { stroke: var(--buy); stroke-width: 1.3;'
                      ' stroke-dasharray: 5 5; }', html,
                      "游标竖虚线的样式与季度基准线不同源：同一张图上会出现两套指示")
        self.assertIn('.stock-price-chart .price-cursor-dot { fill: var(--buy); stroke: #fff; stroke-width: 2; }',
                      html, "游标绿点的样式与季度基准点不同源")
        # 浮层收起时游标一起收：只收浮层的话，绿点会留在图上被读成一个「当前选中」的静态标记。
        self.assertIn('const cursor = priceChartGeo?.cursor;', html, "浮层收起没有带上游标")
        self.assertIn("cursor.group.classList.remove('is-on');", html, "游标缺统一的收起通路")
        self.assertIn('document.addEventListener(\'click\', hidePriceTip);', html,
                      "点击不再收起读数：页面变化后游标会停在旧位置")
        self.assertIn('document.addEventListener(\'scroll\', hidePriceTip, true);', html,
                      "滚动不再收起读数：滚动后游标会停在旧位置")

    def test_stock_page_merges_politicians_and_price_into_one_page(self) -> None:
        # 单股页合并：政客与价格两条页签并成一页；集合数据由 4 格改 3 格，
        # 关联政客那格改成可点进人物页的头像堆叠。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)

        self.assertNotIn('data-stock-tab=', html, "单股页仍有页签按钮")
        self.assertNotIn('stockTab', html, "stockTab 状态变量残留：政客与价格已合并为一页")
        self.assertNotIn('class="stock-tabs"', html, "单股页页签容器还在")
        self.assertNotIn('.stock-tabs {', html, "单股页页签样式还在")
        # 两块正文按顺序同时渲染，缺一块就是合并只做了一半。
        self.assertIn('${politiciansBody}${priceBody}', html, "政客与价格没有同时渲染")
        self.assertIn('<section class="stock-price-section">', html, "价格走势整块丢失")
        self.assertIn('<h2>价格走势</h2>', html, "价格走势标题丢失")

        # 集合数据三格：关联政客 / 官员、现价、披露金额区间。「政客交易 N 笔」已删——
        # 下方「政客交易明细」卡头已经写着当前窗口笔数。
        self.assertIn('.stock-metrics { display: grid; grid-template-columns: repeat(3,1fr);', html,
                      "单股页集合数据不是 3 格")
        self.assertNotIn('${displayTransactionCount}笔', html, "「政客交易 N 笔」那格没删掉")
        self.assertIn("['披露金额区间', amountRange, '', '']", html, "披露金额区间那格结构被改")

        # 关联政客一格：头像必须是真按钮走全局委托，最多 5 枚，余量收进 +N 且姓名可读。
        self.assertIn('const STOCK_AVATAR_LIMIT = 5;', html, "头像上限常量缺失")
        self.assertIn('<div class="stock-metric-people">', html, "关联政客一格没有头像堆叠容器")
        self.assertIn('class="avatar-person-link" type="button" data-person-link="${id}"', html,
                      "关联政客头像不是 data-person-link 按钮：点不进人物页")
        self.assertIn('class="stock-metric-more" title="${restPeopleIds.map(id => PEOPLE[id].display_name).join(\'、\')}">',
                      html, "+N 没把余下姓名列出来：光一个数字读者拿不到是谁")
        self.assertIn('.stock-metric-people .avatar-person-link:first-child { margin-left: 0; }', html,
                      "第一枚头像没有取消负边距：堆叠会整体左移出格")
        # 与热门股票那处同一个坑：圆形靠 overflow: hidden 裁出，img 上的 outline 会被裁掉，
        # 悬停反馈只能画在按钮自己的 box-shadow 上。
        self.assertIn('.stock-metric-people .avatar-person-link:hover,\n'
                      '  .stock-metric-people .avatar-person-link:focus-visible { z-index: 2;', html,
                      "关联政客头像的悬停特效没了")
        people_hover = html.split('.stock-metric-people .avatar-person-link:focus-visible {', 1)[1].split('}', 1)[0]
        self.assertIn('box-shadow: 0 0 0 2px var(--signal)', people_hover,
                      msg="关联政客头像的悬停描边不再走 box-shadow：img 上的 outline 会被 overflow: hidden 裁掉")
        self.assertIn('z-index: 2', people_hover,
                      msg="悬停的头像没有抬到相邻头像之上：描边会被 DOM 在后的头像压掉一半")

        # 现价格：涨跌数字是主角，价格是配角。两条都用复合选择器 (0,2,1) 写在
        # 非 media 覆盖块之前也能赢——它同时压过 .stock-metric b 和 720px 断点里的字号回落。
        # 首屏压缩把两者一起向下调过一次（15/32 → 13/26），倍数仍是 2.0×：
        # 「涨跌数字放大、价格相对变小」讲的是相对关系，只改其中一个数就会改掉这个关系，
        # 所以这里锁的是「两者同时存在且比值不变」，不是一个绝对字号。
        self.assertIn('.stock-metric b.stock-metric-price { margin-top: 2px; color: var(--soft); font: 600 13px/1 var(--mono); }',
                      html, "现价的挂牌价没有缩小：涨跌数字放大了却仍是同一字号，两者分不出主次")
        self.assertIn('.stock-metric b.stock-metric-change { margin-top: 5px; font: 800 26px/1 var(--mono); }',
                      html, "现价的涨跌数字没有放大")
        price_px = int(re.search(r'\.stock-metric b\.stock-metric-price \{[^}]*font: 600 (\d+)px/1', html).group(1))
        change_px = int(re.search(r'\.stock-metric b\.stock-metric-change \{[^}]*font: 800 (\d+)px/1', html).group(1))
        self.assertEqual(change_px, price_px * 2,
                         msg=f"现价的涨跌数字与价格不再是 2 倍关系（{change_px} vs {price_px}）："
                             "两者的主次靠这个倍数，单改一个数就没了")
        self.assertIn('<b class="stock-metric-change ${returnClass(quarterDelta)}">${pricePct(quarterDelta)}</b>', html,
                      "现价那格不再把涨跌百分比单独渲染成一个 <b>")

        # 720px 以下退两列后，第三格必须跨满整行，否则右半边空出一格带竖线的空位。
        self.assertIn('.stock-metric:last-child { grid-column: 1 / -1; border-right: 0; }', html,
                      "窄屏两列时第三格没有跨行：会留下一个空的半格")
        self.assertIn('.stock-metrics { grid-template-columns: repeat(2,1fr); }', html,
                      "单股页集合数据在窄屏没有退两列")

    def test_first_screen_reaches_the_detail_tables(self) -> None:
        # 首屏验收：人物页与单股页的标题区必须压到明细表进入首屏为止。
        # 这条测试锁的是**几个决定高度的数**，不是「某段文字在不在」——改字号时最容易
        # 只改看得见的那几行（名字、摘要），而真正决定高度的另有其人：
        #   人物页 = .person-portrait 的 min-height（右侧身份栏比它矮，hero 高度由它定）；
        #   单股页 = .stock-summary 的行数 × 行高，以及 .stock-glyph 与 .stock-ticker 取大值的那一行。
        # 实测（headless Chromium，1440×800 / 1440×900 / 1280×800 / 1920×1080 四档）：
        #   人物页表头 677px、首行 726px；单股页表头 758px（1280 宽下 778px）。
        # 因此把「压缩前的那几个数」写成 assertNotIn：它们一旦回来，第一屏就再也看不到表。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)

        self.assertIn('.person-portrait { min-height: 300px;', html,
                      "人物肖像高度不是压缩后的 300px：它是人物页 hero 的实际高度来源，"
                      "只缩名字和摘要不会让表格进入首屏")
        self.assertNotIn('min-height: 440px', html, "人物肖像退回了压缩前的 440px")
        self.assertIn('.person-name { max-width: 820px; margin: 12px 0 10px; '
                      'font: 500 clamp(34px,4.2vw,56px)/.94 var(--display);', html,
                      "人物姓名没有压到 56px 上限")
        self.assertNotIn('clamp(54px,7vw,96px)', html, "人物姓名退回了压缩前的 96px 上限")
        self.assertIn('font-size: 13px; line-height: 1.62; }', html,
                      "人物摘要的 13px/1.62 不见了")

        self.assertIn('.stock-glyph { width: 50px; height: 50px;', html,
                      "股票代码方块没有缩到 50px：它会顶高代码行的行高，代码字号再小也没用")
        self.assertNotIn('width: 68px; height: 68px', html, "股票代码方块退回了压缩前的 68px")
        self.assertIn('font: 650 clamp(34px,4.2vw,54px)/.92 var(--mono);', html,
                      "股票代码没有压到 54px 上限")
        self.assertNotIn('clamp(54px,7vw,92px)', html, "股票代码退回了压缩前的 92px 上限")
        self.assertIn('font-size: 12.5px; line-height: 1.55; }', html,
                      "股票摘要的 12.5px/1.55 不见了：它的行数决定 .stock-heading 的高度")

    def test_price_change_figures_are_coloured_green_up_red_down(self) -> None:
        # 涨跌数字必须着色：涨绿、跌红。
        #
        # .positive/.negative 与 .hot-stock-return 同为单类选择器(0,1,0)，同分时靠源码顺序决胜。
        # .hot-stock-return 声明了 color: var(--text)，只要它排在 .positive 之后，就会把涨跌色
        # 压回深灰——这正是「+9.1% 明明带了 class 却不着色」的成因。这里锁定复合选择器(0,2,0)，
        # 让修复不再依赖两个规则块的相对位置（将来谁把 .positive 挪到前面都不会再退化）。
        result = build_snapshot(production_payload())
        html = render_html(result)
        self.assertIn(
            ".positive { color: var(--buy); } .negative { color: var(--sell); }",
            html,
            "涨跌基础色规则缺失（.positive 绿 / .negative 红）",
        )
        self.assertIn(
            ".hot-stock-return.positive { color: var(--buy); }",
            html,
            "缺 .hot-stock-return.positive 复合选择器：单类选择器会被 .hot-stock-return 的 "
            "color: var(--text) 按源码顺序压掉，股价涨跌会重新变成不着色的深灰",
        )
        self.assertIn(
            ".hot-stock-return.negative { color: var(--sell); }",
            html,
            "缺 .hot-stock-return.negative 复合选择器：下跌数字会重新变成不着色的深灰",
        )
        # 单股页「现价」那格的涨跌百分比同理：字放大到 32px 之后更容易被同权规则压掉颜色。
        self.assertIn(
            ".stock-metric-change.positive { color: var(--buy); }",
            html,
            "缺 .stock-metric-change.positive 复合选择器：现价的涨跌数字会变回不着色",
        )
        self.assertIn(
            ".stock-metric-change.negative { color: var(--sell); }",
            html,
            "缺 .stock-metric-change.negative 复合选择器：现价的下跌数字会变回不着色",
        )
        # 非有限值不判方向：NaN >= 0 为 false，会把无行情的「—」误染成红色跌；
        # null/undefined/'' 也要先挡掉，因为 Number(null) === 0 会被当成上涨染绿。
        self.assertIn("if (n === null || n === undefined || n === '') return '';", html)
        self.assertNotIn("const returnClass = n => n >= 0 ? 'positive' : 'negative';", html)
        # pct 与 returnClass 同一套判据：人物页新增的「交易日至今」列会直接吃
        # underlying_return_since_trade，缺行情序列时它是 null，n.toFixed 会当场抛错。
        self.assertIn("if (n === null || n === undefined || n === '') return '—';", html,
                      msg="pct 缺空值判据：没行情序列的记录会把 null 丢给 toFixed 抛错")
        self.assertIn("return Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` : '—';", html,
                      msg="pct 没有把非有限值收敛成 —：无涨跌数据会被算成一个数")
        # 规则再对，也得真的挂上 class 才生效：四处着色点都要接 returnClass。
        self.assertIn("class=\"hot-stock-return ${hasReturn ? returnClass(returnValue) : ''}\"", html)
        self.assertIn('class="return ${returnClass(t.underlying_return_since_trade)}"', html)
        self.assertIn('class="stock-metric-change ${returnClass(quarterDelta)}"', html,
                      msg="单股页现价的涨跌数字没接 returnClass，涨跌都不着色")
        self.assertIn('class="${returnClass(t.underlying_return_since_trade)}">${pct(t.underlying_return_since_trade)}</span>', html,
                      msg="人物页「交易日至今」列没接 returnClass，涨跌都不着色")

    def test_date_label_does_not_shift_across_timezones(self) -> None:
        # 交易日与申报日必须按记录自身的日历日呈现，不能按浏览者本地时区换算。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        for person in result["people"]:
            person["portrait_data_uri"] = "data:image/svg+xml;base64,PHN2Zy8+"
        html = render_html(result)
        self.assertIn('const dateLabel = value => {', html)
        self.assertIn('/^(\\d{4})-(\\d{2})-(\\d{2})/', html)
        self.assertNotIn("new Intl.DateTimeFormat('zh-CN', {month:'short', day:'numeric'})", html)

    def test_demo_priority_people_all_have_both_directions_in_window(self) -> None:
        # 改动 D 的核心契约：14 张重点人物卡在固定 30 天窗口内都必须有买入和卖出记录，
        # 否则新卡片结构会退化成空态。这里在 Python 侧用同一窗口口径复算一遍。
        result = build_snapshot(json.loads(DEMO_PATH.read_text(encoding="utf-8")))
        cutoff = date.fromisoformat(result["meta"]["data_cutoff_at"][:10])
        start = cutoff - timedelta(days=29)
        window = [
            t for t in result["transactions"]
            if start <= date.fromisoformat(t["transaction_date"]) <= cutoff
        ]
        priority = [p for p in result["people"] if p.get("priority")]
        self.assertEqual(len(priority), 14)
        for person in priority:
            own = [t for t in window if t["person_id"] == person["id"]]
            directions = {t["transaction_type"] for t in own}
            self.assertIn("purchase", directions, f"{person['display_name']} 缺少窗口内买入记录")
            self.assertIn("sale", directions, f"{person['display_name']} 缺少窗口内卖出记录")
        # 90 天窗口必须严格覆盖 30 天窗口，两个区块的数据才有对比意义。
        wide = [
            t for t in result["transactions"]
            if cutoff - timedelta(days=89) <= date.fromisoformat(t["transaction_date"]) <= cutoff
        ]
        self.assertGreater(len(wide), len(window))
        self.assertTrue(set(t["id"] for t in window) <= set(t["id"] for t in wide))

    def test_fetch_requests_the_canonical_schema(self) -> None:
        args = SimpleNamespace(
            api_url="https://data.example",
            value=None,
            mode="dashboard",
            force_refresh=False,
            locale="zh-CN",
        )
        request = build_request(args)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["schema_version"], "politician-dashboard/v1")
        self.assertEqual(
            body["include"],
            ["people", "transactions", "reported_holdings", "security_market_data", "source_health"],
        )


if __name__ == "__main__":
    unittest.main()
