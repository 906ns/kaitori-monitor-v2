"""Discord bot 本体: スラッシュコマンドと毎日の価格レポート。"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from .config import Config
from .scraper import (
    KaitoriScraper,
    Product,
    ScrapeError,
    format_price,
    is_jan,
)
from .storage import DuplicateTargetError, Storage, Target

log = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

EMBED_COLOR = 0xF15A24  # サイトのテーマカラー
MAX_FIELDS_PER_EMBED = 25
MAX_EMBEDS_PER_MESSAGE = 10


def parse_notify_time(raw: str) -> datetime.time:
    m = _TIME_RE.match(raw.strip())
    if not m:
        raise ValueError(f"時刻は HH:MM 形式で指定してください: {raw!r}")
    return datetime.time(int(m.group(1)), int(m.group(2)), tzinfo=JST)


def format_diff(price: int, prev: int | None) -> str:
    if prev is None:
        return "初回取得"
    diff = price - prev
    if diff == 0:
        return "前回比 ±0"
    return f"前回比 {diff:+,}円"


class ProductSelectView(discord.ui.View):
    """商品名検索で複数ヒットしたときの選択メニュー。"""

    def __init__(self, bot: "KaitoriBot", products: list[Product], query: str):
        super().__init__(timeout=180)
        self._bot = bot
        self._products = {p.class_id: p for p in products}
        self._query = query
        options = [
            discord.SelectOption(
                label=p.name[:100],
                value=p.class_id,
                description=f"新品 {format_price(p.price_new)} / JAN {p.jan or '-'}"[:100],
            )
            for p in products[:MAX_FIELDS_PER_EMBED]
        ]
        select = discord.ui.Select(placeholder="監視する商品を選んでください", options=options)
        select.callback = self._on_select
        self._select = select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        product = self._products[self._select.values[0]]
        message = await self._bot.register_target(product, self._query)
        self._select.disabled = True
        self.stop()
        await interaction.response.edit_message(content=message, view=self)


class KaitoriBot(commands.Bot):
    def __init__(self, config: Config, storage: Storage, scraper: KaitoriScraper):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.storage = storage
        self.scraper = scraper
        self.daily_loop: tasks.Loop | None = None

    # --- 起動処理 ---

    async def setup_hook(self) -> None:
        await self._sync_yaml_targets()
        self._start_daily_loop()
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("ログイン完了: %s (通知時刻 %s)", self.user, self.notify_time_str())

    async def _sync_yaml_targets(self) -> None:
        """targets.yaml の対象を DB に取り込む(登録済みはスキップ)。"""
        for entry in self.config.yaml_targets:
            jan = entry.get("jan")
            name = entry.get("name")
            try:
                if jan:
                    if self.storage.has_target_jan(jan):
                        continue
                    product = await asyncio.to_thread(self.scraper.find_by_jan, jan)
                    if product is None:
                        log.warning("targets.yaml: JAN %s が見つかりません", jan)
                        continue
                    await self.register_target(product, jan)
                elif name:
                    products = await asyncio.to_thread(self.scraper.search, name)
                    products = [p for p in products if not self.storage.has_target(p.class_id)]
                    if len(products) == 1:
                        await self.register_target(products[0], name)
                    elif len(products) > 1:
                        exact = [p for p in products if p.name == name]
                        if len(exact) == 1:
                            await self.register_target(exact[0], name)
                        else:
                            log.warning(
                                "targets.yaml: %r は %d 件ヒットするため取り込みません。"
                                "/add で選択して登録してください",
                                name, len(products),
                            )
                    else:
                        log.warning("targets.yaml: %r が見つかりません", name)
            except (ScrapeError, DuplicateTargetError) as exc:
                log.warning("targets.yaml の取り込みに失敗: %s", exc)

    # --- 定期通知 ---

    def notify_time_str(self) -> str:
        return self.storage.get_setting("notify_time") or self.config.yaml_notify_time

    def _start_daily_loop(self) -> None:
        if self.daily_loop is not None:
            self.daily_loop.cancel()
        try:
            at = parse_notify_time(self.notify_time_str())
        except ValueError:
            log.warning("通知時刻 %r が不正なため 09:00 を使います", self.notify_time_str())
            at = parse_notify_time("09:00")
        loop = tasks.loop(time=at)(self._daily_report)
        loop.before_loop(self.wait_until_ready)
        self.daily_loop = loop
        loop.start()

    async def _daily_report(self) -> None:
        try:
            channel = self.get_channel(self.config.channel_id) or await self.fetch_channel(
                self.config.channel_id
            )
            embeds = await self.build_price_report(record=True)
            for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
                await channel.send(embeds=embeds[i : i + MAX_EMBEDS_PER_MESSAGE])
        except Exception:
            log.exception("定期通知の送信に失敗しました")

    # --- 共通処理 ---

    async def register_target(self, product: Product, query: str) -> str:
        """商品を監視対象に登録し、結果メッセージを返す。"""
        try:
            target = self.storage.add_target(
                product.class_id, product.jan, product.name, query
            )
        except DuplicateTargetError:
            return f"⚠️ **{product.name}** は既に監視対象に登録されています"
        if product.price_new is not None:
            self.storage.record_price(target.id, product.price_new)
        return (
            f"✅ **{product.name}** を監視対象に追加しました\n"
            f"JAN: {product.jan or '-'} / 新品買取: {format_price(product.price_new)}"
        )

    def _fetch_target_sync(self, target: Target) -> Product | None:
        """登録済み対象の現在の商品情報を取得する。見つからなければ None。"""
        keyword = target.jan or target.query or target.name
        products = self.scraper.search(keyword)
        for p in products:
            if p.class_id == target.class_id:
                return p
        if target.jan:
            for p in products:
                if p.jan == target.jan:
                    return p
        return None

    async def build_price_report(self, record: bool) -> list[discord.Embed]:
        """全監視対象の現在価格レポートを作る。record=True なら履歴に記録する。"""
        targets = self.storage.list_targets()
        today = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
        if not targets:
            embed = discord.Embed(
                title="買取商店 価格レポート",
                description=f"{today}\n監視対象がありません。/add で登録してください。",
                color=EMBED_COLOR,
            )
            return [embed]

        fields: list[tuple[str, str]] = []
        for target in targets:
            try:
                product = await asyncio.to_thread(self._fetch_target_sync, target)
            except ScrapeError:
                product = None
                if record:
                    self.storage.record_price(target.id, None, status="error")
                fields.append((target.name, "⚠️ 取得失敗(通信エラー)"))
                continue

            if product is None:
                if record:
                    self.storage.record_price(target.id, None, status="not_found")
                fields.append((target.name, "⚠️ 取得失敗(サイトで見つかりません)"))
            elif product.price_new is None:
                if record:
                    self.storage.record_price(target.id, None, status="no_price")
                fields.append((target.name, "⚠️ 価格を読み取れませんでした"))
            else:
                prev = self.storage.last_ok_price(target.id)
                diff = format_diff(product.price_new, prev)
                if record:
                    self.storage.record_price(target.id, product.price_new)
                fields.append(
                    (
                        target.name,
                        f"新品買取: **{format_price(product.price_new)}** ({diff})\n"
                        f"JAN: {target.jan or '-'}",
                    )
                )

        embeds: list[discord.Embed] = []
        for i in range(0, len(fields), MAX_FIELDS_PER_EMBED):
            embed = discord.Embed(
                title="買取商店 価格レポート" if i == 0 else "買取商店 価格レポート(続き)",
                description=today if i == 0 else None,
                color=EMBED_COLOR,
            )
            for name, value in fields[i : i + MAX_FIELDS_PER_EMBED]:
                embed.add_field(name=name[:256], value=value[:1024], inline=False)
            embeds.append(embed)
        return embeds


def create_bot(config: Config, storage: Storage, scraper: KaitoriScraper) -> KaitoriBot:
    bot = KaitoriBot(config, storage, scraper)

    @bot.tree.command(name="add", description="監視対象を追加します(JANコードまたは商品名)")
    @discord.app_commands.describe(keyword="JANコードまたは商品名")
    async def add(interaction: discord.Interaction, keyword: str) -> None:
        await interaction.response.defer()
        keyword = keyword.strip()
        try:
            if is_jan(keyword):
                product = await asyncio.to_thread(scraper.find_by_jan, keyword)
                if product is None:
                    await interaction.followup.send(
                        f"❌ JAN `{keyword}` に該当する商品が見つかりませんでした"
                    )
                    return
                await interaction.followup.send(await bot.register_target(product, keyword))
                return

            products = await asyncio.to_thread(scraper.search, keyword)
        except ScrapeError:
            await interaction.followup.send("❌ サイトへのアクセスに失敗しました。しばらくしてから再試行してください")
            return

        if not products:
            await interaction.followup.send(f"❌ 「{keyword}」に該当する商品が見つかりませんでした")
        elif len(products) == 1:
            await interaction.followup.send(await bot.register_target(products[0], keyword))
        else:
            note = ""
            if len(products) > MAX_FIELDS_PER_EMBED:
                note = f"\n(ヒット {len(products)} 件のうち先頭 {MAX_FIELDS_PER_EMBED} 件を表示。絞り込むにはより具体的な商品名で再実行してください)"
            await interaction.followup.send(
                f"「{keyword}」は {len(products)} 件ヒットしました。監視する商品を選んでください{note}",
                view=ProductSelectView(bot, products, keyword),
            )

    @bot.tree.command(name="remove", description="監視対象を削除します(/list の番号またはJANコード)")
    @discord.app_commands.describe(target="/list に表示される番号、またはJANコード")
    async def remove(interaction: discord.Interaction, target: str) -> None:
        target = target.strip()
        targets = bot.storage.list_targets()
        found: Target | None = None
        if target.isdigit() and 1 <= int(target) <= len(targets):
            found = targets[int(target) - 1]
        else:
            for t in targets:
                if target in (t.jan, t.class_id) or target == t.name:
                    found = t
                    break
        if found is None:
            await interaction.response.send_message(
                f"❌ 「{target}」に一致する監視対象がありません。/list で確認してください"
            )
            return
        bot.storage.remove_target(found.id)
        await interaction.response.send_message(f"🗑️ **{found.name}** を監視対象から削除しました")

    @bot.tree.command(name="list", description="監視対象の一覧を表示します")
    async def list_(interaction: discord.Interaction) -> None:
        targets = bot.storage.list_targets()
        if not targets:
            await interaction.response.send_message("監視対象がありません。/add で登録してください")
            return
        lines = []
        for i, t in enumerate(targets, start=1):
            last = bot.storage.last_ok_price(t.id)
            price = f"前回 {format_price(last)}" if last is not None else "価格未取得"
            lines.append(f"{i}. **{t.name}** (JAN: {t.jan or '-'}) — {price}")
        embed = discord.Embed(
            title=f"監視対象一覧 ({len(targets)}件)",
            description="\n".join(lines)[:4096],
            color=EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="price", description="現在の買取価格をその場で照会します")
    @discord.app_commands.describe(keyword="省略すると全監視対象。指定するとその語で検索(未登録商品も可)")
    async def price(interaction: discord.Interaction, keyword: str | None = None) -> None:
        await interaction.response.defer()
        if keyword is None:
            embeds = await bot.build_price_report(record=False)
            for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
                await interaction.followup.send(embeds=embeds[i : i + MAX_EMBEDS_PER_MESSAGE])
            return
        try:
            products = await asyncio.to_thread(scraper.search, keyword.strip())
        except ScrapeError:
            await interaction.followup.send("❌ サイトへのアクセスに失敗しました。しばらくしてから再試行してください")
            return
        if not products:
            await interaction.followup.send(f"❌ 「{keyword}」に該当する商品が見つかりませんでした")
            return
        lines = [
            f"**{p.name}** — 新品: {format_price(p.price_new)} / 中古: {format_price(p.price_used)} (JAN: {p.jan or '-'})"
            for p in products[:20]
        ]
        if len(products) > 20:
            lines.append(f"…ほか {len(products) - 20} 件")
        embed = discord.Embed(
            title=f"「{keyword}」の検索結果",
            description="\n".join(lines)[:4096],
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="settime", description="毎日の通知時刻を変更します(JST)")
    @discord.app_commands.describe(time="HH:MM 形式 (例: 09:30)")
    async def settime(interaction: discord.Interaction, time: str) -> None:
        try:
            parsed = parse_notify_time(time)
        except ValueError:
            await interaction.response.send_message("❌ 時刻は HH:MM 形式で指定してください (例: 09:30)")
            return
        normalized = f"{parsed.hour:02d}:{parsed.minute:02d}"
        bot.storage.set_setting("notify_time", normalized)
        bot._start_daily_loop()
        await interaction.response.send_message(f"⏰ 毎日 {normalized} (JST) に価格レポートを通知します")

    return bot


def run(config: Config) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    storage = Storage(config.db_path)
    scraper = KaitoriScraper()
    bot = create_bot(config, storage, scraper)
    try:
        bot.run(config.token, log_handler=None)
    finally:
        storage.close()
