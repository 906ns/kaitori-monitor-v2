"""検索結果 HTML パーサのテスト(実サイトの構造を模したフィクスチャを使用)。"""
from kaitori_monitor.scraper import format_price, is_jan, parse_search_result

# 実サイト(2026-07-07 取得)の検索結果から構造を抜粋したもの
FIXTURE = """
<table>
<tr id="ex-product-21140" class="price_list_item class_list_20802">
  <td class="align-middle"><img src="/x.jpg"></td>
  <td class="align-middle">
    iPhone 16 Pro Max 256GB 金
    <div class="item-desc">
      <span class="product-code-default">JAN:</span>
      <span class="product-code-default">4549995536447</span>
    </div>
    <div class="item-desc"><span class="new-product-icon">買取強化</span></div>
  </td>
  <td class="align-middle col-product-price">
    <div class="item-price encrypt-price plain-price">188,000円</div>
  </td>
  <td class="align-middle col-product-price">
    <div class="item-price encrypt-price plain-price">137,000円</div>
  </td>
</tr>
<tr id="ex-product-99999" class="price_list_item class_list_88888">
  <td class="align-middle"><img src="/y.jpg"></td>
  <td class="align-middle">
    JANなし商品
    <div class="item-desc"></div>
  </td>
  <td class="align-middle col-product-price">
    <div class="item-price">お問い合わせ</div>
  </td>
</tr>
</table>
"""


def test_parse_search_result():
    products = parse_search_result(FIXTURE)
    assert len(products) == 2

    p = products[0]
    assert p.class_id == "21140"
    assert p.product_id == "20802"
    assert p.name == "iPhone 16 Pro Max 256GB 金"
    assert p.jan == "4549995536447"
    assert p.price_new == 188000
    assert p.price_used == 137000

    q = products[1]
    assert q.class_id == "99999"
    assert q.name == "JANなし商品"
    assert q.jan is None
    assert q.price_new is None  # 価格が数値でない場合は None
    assert q.price_used is None


def test_parse_empty():
    assert parse_search_result("<div>該当なし</div>") == []


def test_is_jan():
    assert is_jan("4549995536447")
    assert is_jan(" 49123456 ")
    assert not is_jan("iPhone 16")
    assert not is_jan("123")


def test_format_price():
    assert format_price(188000) == "188,000円"
    assert format_price(None) == "-"
