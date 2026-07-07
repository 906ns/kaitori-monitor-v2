"""買取商店 (kaitorishouten-co.jp) サイト内検索スクレイパー。

検索は AJAX の POST エンドポイントを直接叩く。
X-Requested-With ヘッダが無いと 404、ブラウザ相当の User-Agent が無いと 403 になる。

単体実行: python -m kaitori_monitor.scraper <JANまたは商品名>
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.kaitorishouten-co.jp"
SEARCH_URL = f"{BASE_URL}/products/list/keyword"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# アクセスマナー: リクエスト間の最小間隔(秒)
REQUEST_INTERVAL_SEC = 2.5
TIMEOUT_SEC = 30
MAX_RETRIES = 3

_JAN_RE = re.compile(r"^\d{8,14}$")
_PRICE_RE = re.compile(r"([\d,]+)\s*円")


class ScrapeError(Exception):
    """検索リクエストがリトライしても成功しなかった。"""


@dataclass
class Product:
    class_id: str  # 商品クラスID (tr の id="ex-product-<class_id>")。監視対象の一意キー
    product_id: str | None
    name: str
    jan: str | None
    price_new: int | None  # 新品買取価格(円)
    price_used: int | None  # 中古買取価格(円)


def is_jan(keyword: str) -> bool:
    return bool(_JAN_RE.match(keyword.strip()))


def format_price(price: int | None) -> str:
    return f"{price:,}円" if price is not None else "-"


def _parse_price(text: str) -> int | None:
    m = _PRICE_RE.search(text)
    return int(m.group(1).replace(",", "")) if m else None


def parse_search_result(html: str) -> list[Product]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[Product] = []
    for tr in soup.select("tr.price_list_item"):
        tr_id = tr.get("id", "")
        if not tr_id.startswith("ex-product-"):
            continue
        class_id = tr_id.removeprefix("ex-product-")

        product_id = None
        for cls in tr.get("class", []):
            if cls.startswith("class_list_"):
                product_id = cls.removeprefix("class_list_")

        # 商品名は td 直下のテキストノード(価格や画像の td は直下テキストが空)
        name = ""
        for td in tr.find_all("td", recursive=False):
            direct = "".join(td.find_all(string=True, recursive=False)).strip()
            if direct:
                name = direct
                break

        jan = None
        for span in tr.select("span.product-code-default"):
            text = span.get_text(strip=True)
            if _JAN_RE.match(text):
                jan = text
                break

        prices = [
            _parse_price(div.get_text(strip=True))
            for div in tr.select("td.col-product-price div.item-price")
        ]
        price_new = prices[0] if len(prices) > 0 else None
        price_used = prices[1] if len(prices) > 1 else None

        if name:
            products.append(
                Product(class_id, product_id, name, jan, price_new, price_used)
            )
    return products


class KaitoriScraper:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._last_request = 0.0
        self._initialized = False

    def _throttle(self) -> None:
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _ensure_session(self) -> None:
        # 初回にトップページを取得してセッション Cookie を得る
        if self._initialized:
            return
        self._throttle()
        try:
            self._session.get(f"{BASE_URL}/", timeout=TIMEOUT_SEC)
        except requests.RequestException:
            pass  # Cookie 無しでも検索は通ることがあるため続行
        self._initialized = True

    def search(self, keyword: str) -> list[Product]:
        """商品名または JAN コードで検索し、ヒットした商品を返す。"""
        self._ensure_session()
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            if attempt:
                time.sleep(2**attempt)
            self._throttle()
            try:
                res = self._session.post(
                    SEARCH_URL,
                    data={"name": keyword, "page_type": "1"},
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{BASE_URL}/",
                    },
                    timeout=TIMEOUT_SEC,
                )
                res.raise_for_status()
                return parse_search_result(res.text)
            except requests.RequestException as exc:
                last_error = exc
        raise ScrapeError(f"検索に失敗しました: {keyword}") from last_error

    def find_by_jan(self, jan: str) -> Product | None:
        """JAN コードで検索し、一致する商品を返す。"""
        products = self.search(jan)
        for p in products:
            if p.jan == jan:
                return p
        if len(products) == 1:
            return products[0]
        return None


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m kaitori_monitor.scraper <JANまたは商品名>")
        sys.exit(1)
    scraper = KaitoriScraper()
    products = scraper.search(" ".join(sys.argv[1:]))
    if not products:
        print("該当する商品が見つかりませんでした")
        return
    for p in products:
        print(
            f"[{p.class_id}] {p.name}  JAN:{p.jan or '-'}  "
            f"新品:{format_price(p.price_new)}  中古:{format_price(p.price_used)}"
        )


if __name__ == "__main__":
    main()
