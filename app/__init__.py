from flask import Flask, render_template, request, redirect, url_for
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

def scrape_site(url):
    """指定されたURLからタイトルをスクレイピングする"""
    try:
        response = requests.get(url)

        # ステータスコードが200（成功）の場合にスクレイピングを実行
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')

            # ページのタイトルを取得
            title = soup.title.string if soup.title else "タイトルがありません。"
            # 在庫情報を取得
            stock_status = "在庫情報がありません。"
            # ここでは仮に、HTML内のクラス名 'stock-status' を使って在庫情報を取得する例とします
            stock_element = soup.find(class_='stock')
            
            if stock_element:
                stock_status = stock_element.get_text(strip=True)

            return title, stock_status
        else:
            return None, f"Failed to retrieve the page, status code: {response.status_code}"

    except requests.exceptions.RequestException as e:
        return f"Error: {e}"

@app.route('/', methods=['GET', 'POST'])
def index():
    title = None
    error = None
    if request.method == 'POST':
        # フォームから入力されたURLを取得
        url = request.form.get('url')

        if url:
            # URLが入力されていればスクレイピングを実行
            title, stock_status = scrape_site(url)
        else:
            # URLが入力されていない場合のエラーメッセージ
            error = "URLを入力して下さい。"

        # フォームから結果ページに遷移
        return render_template('result.html', title=title, stock_status=stock_status, error=error)

    return render_template('index.html')

@app.route('/back', methods=['GET'])
def back():
    """結果ページから戻るリンクを押したときにフォームページにリダイレクト"""
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)