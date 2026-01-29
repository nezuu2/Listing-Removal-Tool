import requests
from bs4 import BeautifulSoup

# スクレイピングするURL
url = 'https://www.magicardshop.jp/product/2771'

# ウェブページのHTMLを取得
response = requests.get(url)

# ステータスコードが200の場合、正常に取得
if response.status_code == 200:
    # HTMLを解析
    soup = BeautifulSoup(response.text, 'html.parser')

    # 例: 'stock'というクラス名の在庫情報を探す
    stock_info = soup.find(class_='stock')
    
    if stock_info:
        stock_count = stock_info.text.strip()
        print(f"現在の在庫数: {stock_count}")
        
        # 在庫が0でない場合（商品が売れていない）
        if "売り切れ" not in stock_count:
            print("商品はまだ販売中です")
        else:
            print("商品は売り切れです")
    else:
        print("在庫情報が見つかりませんでした")
    
    # ページタイトルを抽出
    title = soup.title.text
    print(f"ページタイトル: {title}")
else:
    print(f"ページの取得に失敗しました。ステータスコード: {response.status_code}")
