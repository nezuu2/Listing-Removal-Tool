from flask import Flask, render_template, request, redirect, url_for
import requests
from bs4 import BeautifulSoup
# 1. WebDriverとサービス管理
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

# 2. 要素の検索と操作
from selenium.webdriver.common.by import By

# 3. 明示的な待機
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC # ★ 今回の解決策

# 4. 例外処理
from selenium.common.exceptions import TimeoutException, NoSuchElementException 
# NoSuchElementException (要素が見つからなかった場合)もよく使われます

# 5. オプション設定 (任意)
from selenium.webdriver.chrome.options import Options

import time
from datetime import datetime

import logging

from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import pandas as pd
from google.oauth2 import service_account
import gspread
import os
from oauth2client.service_account import ServiceAccountCredentials

from ebaysdk.trading import Connection as Trading
from ebaysdk.exception import ConnectionError

from datetime import datetime, timedelta

import json
import pickle,os
import re
import traceback
import sys

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr

from urllib.parse import quote, urlparse, unquote

import socket

app = Flask(__name__)


# --- ここを既存の send_notification_email 定義と置き換えてください ---
def send_notification_email(receiver_email, merchandise, sku=None, url=None, sender_email="nezuu.mail2@gmail.com", password="lwjxsrmqjzxhsjhm"):
    """Send notification email about a sold-out merchandise.

    If `sku` is provided, subject/body will include it as "{sku}の{merchandise}が売り切れました!".

    Returns True on success, False on failure.
    """
    if sku is None and url:
        sku = extract_sku_from_url(url)
        
    server = None
    try:
        socket.setdefaulttimeout(180)

        if sku:
            subject = f'{sku}の{merchandise}が売り切れました!'
        else:
            subject = f'{merchandise}が売り切れました!'
        # デバッグ用: 送信する件名を出力
        print(f"[メール件名] {subject}")

        msg = MIMEMultipart()
        msg["Subject"] = Header(subject, "utf-8")

        sender_name = 'JANNA'
        msg["From"] = formataddr((str(Header(sender_name, "utf-8")), sender_email))
        msg["To"] = receiver_email

        if sku:
            body = f'メルカリから、{sku}の{merchandise}が売り切れたことを報告いたします。'
        else:
            body = f'メルカリから、{merchandise}が売り切れたことを報告いたします（SKU不明）。'
        part = MIMEText(body, "plain", "utf-8")
        msg.attach(part)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.ehlo()
        server.login(sender_email, password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        try:
            server.quit()
        except Exception:
            pass
        print("メールが正常に送信されました")
        return True
    except Exception as e:
        print(f"メール送信エラー: {e}")
        traceback.print_exc()
        try:
            if server:
                server.quit()
        except Exception:
            pass
    return False
# --- 置換ここまで ---

def extract_sku_from_url(url):
    """URL から item_id を抽出して '#me_<item_id>' 形式の SKU を返す。失敗時は None を返す。"""
    try:
        parsed = urlparse(url)
        path = parsed.path or ''
        # 末尾スラッシュを除去して最後のパートを取り出す
        item_id = unquote(path.rstrip('/').rsplit('/', 1)[-1])
        if not item_id:
            return None
        return f"#me_{item_id}"
    except Exception:
        return None

# スプレッドシートIDをグローバル変数として定義
SPREADSHEET_ID = '1GRdgQNSG1KR_lTTK_fwIXkO7nq4d0oWlJJ8P5wK26Oc'

soldout=False
login_success=False
target_item=""

def scrape_page(interval_minutes=180):

    global soldout
    global target_item
    global stop_item

    stop_item = None

    # 無限ループで定期的にチェック
    while True:

        # 現在のスクリプトのディレクトリを取得
        current_dir = os.path.dirname(os.path.abspath(__file__))

        # 認証設定
        SERVICE_ACCOUNT_FILE = os.path.join(current_dir, "auth", "merukari-455102-152976d85f9f.json")
        SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)

        # Google Sheets APIサービスを構築
        service = build('sheets', 'v4', credentials=credentials)

        try:
            try:
                # スプレッドシートのタブ情報を取得
                meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties").execute()
                sheets = meta.get("sheets", [])
                if not sheets:
                    raise ValueError("スプレッドシートにシートが見つかりませんでした。")

                # シート名リストを作成
                sheet_names = [s["properties"].get("title", "徳重") for s in sheets]
                print(f"参照するシート一覧: {sheet_names}")

                # --- process_sheet の置換（scrape_page() 内） ---
                # タブを一つずつ処理するヘルパー（最小限の処理：F列からURLを取得して既存ロジックへ渡す）
                def process_sheet(sheat_name):
                    nonlocal service
                    RANGE_NAME = f"{sheat_name}!F3:F1000"
                    print(f"参照中のシート: {sheat_name} | 取得範囲: {RANGE_NAME}")
                    sheet_api = service.spreadsheets()
                    result = sheet_api.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
                    values = result.get('values', [])
                    if not values:
                        print(f"{sheat_name} にデータがありません。")
                        return

                    urls = [row[0] for row in values if row and isinstance(row[0], str) and row[0].startswith('https://')]
                    print(f'抽出されたURLリスト({sheat_name}): {urls}')
                    if not urls:
                        return

                    # オプションの設定
                    options = Options()
                    # ヘッドレスモード（画面を表示しない設定）にする場合はコメントアウトを外す
                    options.add_argument('--headless') 
                    options.add_argument('--no-sandbox')
                    options.add_argument('--disable-dev-shm-usage')
                    options.add_argument('--ignore-certificate-errors')
                    options.add_argument('--ignore-ssl-errors')
                    options.add_argument('--disable-web-security')
                    options.add_experimental_option('excludeSwitches', ['enable-logging'])
                    driver = webdriver.Chrome(options=options)
                    cookies_file = 'morokoshi.pkl' # クッキーを保存するファイルの名前
                    # ユーザーエージェントの偽装（ボット対策されているサイト用）
                    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36')

                    service = ChromeService(ChromeDriverManager().install())
                    driver = webdriver.Chrome(service=service, options=options)

                    sent_merchandise = set()
                    for url in urls:
                        # URLごとに状態をリセット
                        soldout = False
                        merchandise = None

                        try:
                            driver = webdriver.Chrome(service=service, options=options)
                            driver.get(url)
                            print(f"アクセス: {url}")
                            try:
                                WebDriverWait(driver, 10).until(
                                    EC.presence_of_element_located((By.XPATH, "//button[contains(text(), '売り切れました')]"))
                                )
                                soldout = True

                                # 商品名取得（必要に応じて XPath を調整）
                                try:
                                    merchandise_el = WebDriverWait(driver, 10).until(
                                        EC.presence_of_element_located((By.XPATH, "//h1"))
                                    )
                                    merchandise = merchandise_el.text
                                except Exception:
                                    merchandise = url  # 取得できなければURLで代替

                                if merchandise not in sent_merchandise:
                                    receiver_email = str(request.form.get('mail'))
                                    try:
                                        item_id = url.rstrip('/').rsplit('/', 1)[-1]
                                        sku_here = f"#me_{item_id}"
                                        print(f'sku_here: {sku_here}')
                                    except Exception:
                                        sku_here = None
                                    success = send_notification_email(receiver_email, merchandise, sku=sku_here)
                                    if success:
                                        sent_merchandise.add(merchandise)
                                        print(f"メール送信済み: {merchandise}")

                                        # --- eBay出品取り下げ処理 ---
                                        try:
                                            appid='MANABUKU-myapp-PRD-dfe4c757a-0849e252'
                                            devid='37712345-e36e-4afb-80d9-3f8273f52f08'
                                            certid='PRD-fe4c757a9132-1139-4a19-a2ca-4325'
                                            token='v^1.1#i^1#r^1#f^0#p^3#I^3#t^Ul4xMF85OjFGMjJEQzM2MTQ2QzlCRDYxRjkxQjhBNDhBQzY2MTVFXzBfMSNFXjI2MA=='

                                            # ユーザー情報を取得するAPI (疎通確認用)
                                            try:
                                                api = Trading(appid=appid, devid=devid, certid=certid, token=token, config_file=None)
                                                # response = api.execute('GetUser', {})
                                            except ConnectionError as e:
                                                print(f"eBay Connection Error: {e}")

                                            # 検索したいSKU
                                            if sku_here:
                                                target_sku = sku_here
                                            else:
                                                print("SKUが取得できなかったため、eBay処理をスキップします。")
                                                target_sku = None

                                            if target_sku:
                                                # 過去30日間の出品を取得
                                                start_time = datetime.now() - timedelta(days=30)
                                                end_time = datetime.now()

                                                stop_item = None
                                                try:
                                                    response = api.execute('GetSellerList', {
                                                        'StartTimeFrom': start_time.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                                                        'StartTimeTo': end_time.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                                                        'DetailLevel': 'ReturnAll',
                                                        'Pagination': {
                                                            'EntriesPerPage': 200,
                                                            'PageNumber': 1
                                                        }
                                                    })
                                                    
                                                    # SKUで商品をフィルタリング
                                                    matching_items = []
                                                    if hasattr(response.reply, 'ItemArray') and hasattr(response.reply.ItemArray, 'Item'):
                                                        for item in response.reply.ItemArray.Item:
                                                            if hasattr(item, 'SKU') and item.SKU == target_sku:
                                                                matching_items.append({
                                                                    'ItemID': item.ItemID,
                                                                    'Title': item.Title,
                                                                    'SKU': item.SKU,
                                                                    'Price': item.StartPrice.value if hasattr(item, 'StartPrice') else 'N/A'
                                                                })
                                                    
                                                    if matching_items:
                                                        print(f"{len(matching_items)}件の商品が見つかりました:")
                                                        for item in matching_items:
                                                            print(f"ItemID: {item['ItemID']}, Title: {item['Title']}, SKU: {item['SKU']}, Price: {item['Price']}")
                                                            stop_item = item['ItemID']
                                                            print("取り下げたい商品(stop_item): " + stop_item)
                                                    else:
                                                        print(f"SKU '{target_sku}' の商品は見つかりませんでした。")
                                                except ConnectionError as e:
                                                    print(f"eBay APIリクエストでエラーが発生しました: {e}")
                                                    traceback.print_exc()

                                                if stop_item is not None:
                                                    # 取り下げ前に出品状態を確認するヘルパー
                                                    def is_item_active(api_obj, item_id):
                                                        try:
                                                            resp = api_obj.execute('GetItem', {'ItemID': item_id})
                                                            reply = getattr(resp, 'reply', None)
                                                            item = None
                                                            if reply is not None and hasattr(reply, 'Item'):
                                                                item = reply.Item
                                                            
                                                            # ListingDetails.EndTime check
                                                            try:
                                                                end_time_str = getattr(getattr(item, 'ListingDetails', None), 'EndTime', None)
                                                                if end_time_str:
                                                                    try:
                                                                        end_time = datetime.strptime(end_time_str, '%Y-%m-%dT%H:%M:%S.%fZ')
                                                                    except Exception:
                                                                        try:
                                                                            end_time = datetime.strptime(end_time_str, '%Y-%m-%dT%H:%M:%SZ')
                                                                        except Exception:
                                                                            end_time = None
                                                                    if end_time is not None and end_time < datetime.utcnow():
                                                                        return False
                                                            except Exception:
                                                                pass

                                                            # SellingStatus check
                                                            try:
                                                                selling_status = getattr(item, 'SellingStatus', None)
                                                                listing_status = getattr(selling_status, 'ListingStatus', None)
                                                                if listing_status and str(listing_status).lower() == 'ended':
                                                                    return False
                                                            except Exception:
                                                                pass

                                                            return True
                                                        except Exception as e:
                                                            print(f"GetItem チェックでエラーが発生しました: {e}")
                                                            return False

                                                    # 出品がまだアクティブなら取り下げを実行
                                                    if is_item_active(api, stop_item):
                                                        response = api.execute('EndFixedPriceItem', {
                                                            'ItemID': stop_item,
                                                            'EndingReason': 'NotAvailable'
                                                        })
                                                        print("出品の取り下げが成功しました。")
                                                        print(f"Status: {response.reply.Ack}")
                                                        
                                                        # ログ保存
                                                        logger = logging.getLogger()
                                                        for handler in logger.handlers[:]:
                                                            logger.removeHandler(handler)
                                                        file_handler = logging.FileHandler('output.log')
                                                        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                                                        file_handler.setFormatter(formatter)
                                                        logger.addHandler(file_handler)
                                                        stream_handler = logging.StreamHandler(sys.stdout)
                                                        stream_handler.setFormatter(formatter)
                                                        logger.addHandler(stream_handler)
                                                        logger.info(f"Item {stop_item} removed successfully.")

                                                    else:
                                                        print(f"Item {stop_item} は既に終了しているため、取り下げをスキップします。")
                                                else:
                                                    print("取り下げる商品が見つかりませんでした。")

                                        except Exception as e:
                                            print(f"eBay処理中にエラーが発生しました: {e}")
                                            traceback.print_exc()
                                        # --- eBay出品取り下げ処理 ここまで ---

                                    else:
                                        print(f"メール送信失敗: {merchandise}")
                                else:
                                    print(f"{merchandise} は既に送信済み")
                            except TimeoutException:
                                print("売り切れボタンが見つかりませんでした（在庫あり）")
                            except Exception as e:
                                print(f"Selenium エラー: {e}")
                                traceback.print_exc()
                            finally:
                                try:
                                    driver.quit()
                                except Exception:
                                    pass
                        except Exception as e:
                            print(f"ドライバー起動/アクセスでエラー: {e}")
                            traceback.print_exc()

                # すべてのタブを順番に処理
                for sheat_name in sheet_names:
                    try:
                        process_sheet(sheat_name)
                    except Exception as e:
                        print(f"{sheat_name} の処理中にエラー: {e}")
                        traceback.print_exc()
    # --- 置換ここまで ---

                # 全タブ処理が終わったらこの関数を抜ける
                # return # ループさせるためにコメントアウト
            except Exception as e:
                print(f"スプレッドシートのタブ取得エラー: {e}")
                traceback.print_exc()
        
        except Exception as e:
            print(f"エラー: {type(e).__name__}")
            print("詳細: ")
            traceback.print_exc()

        # 指定された時間待機してから再実行
        print(f"{interval_minutes}分待機します...")
        time.sleep(interval_minutes * 60)


def wait_until_target_time(target_time_str):
    # 現在時刻を取得
    current_time = datetime.now()
    # ターゲット時間（文字列からdatetimeオブジェクトへ変換）
    target_time = datetime.strptime(target_time_str, "%H:%M")
    
    # 現在の日時にターゲット時間の時間と分をセット
    target_time = target_time.replace(year=current_time.year, month=current_time.month, day=current_time.day)
    
    # もしターゲット時間が過ぎていたら、明日の同じ時刻に設定
    if target_time < current_time:
        target_time = target_time.replace(day=current_time.day + 1)
    
    # 現在時刻とターゲット時間の差を計算
    time_to_wait = (target_time - current_time).total_seconds()
    print(f"Waiting for {time_to_wait} seconds until {target_time_str}...")
    
    # ターゲット時間まで待機
    time.sleep(time_to_wait)

@app.route('/', methods=['GET', 'POST'])
def index():
    title = None
    error = None
    card = None
    stock_status = None
    target_time = None
    target_time2 = None

    stock_text = None

    if request.method == 'POST':
        # フォームから入力されたURLを取得
        # card = request.form.get('card_name')
        mail_status = request.form.get('mail')
        # 実行したい時刻（例: 14:30に実行）
        target_time = request.form.get('checktime')
        target_time2 = request.form.get('checktime2')
        # print("検索するカード：")
        print("待機する時間：" + str(target_time) + " " + str(target_time2))

        render_result = render_template('result.html', title=title, stock_status=stock_status)

        # ループ間隔を取得（デフォルト180分）
        try:
            interval = int(request.form.get('interval', 180))
        except ValueError:
            interval = 180

        if mail_status:

            if target_time == "":
                        target_time = None
            if target_time2 == "":
                target_time2 = None

            # target_timeに数字が入っていたら実行する
            if target_time is not None and target_time2 is not None:
                current_time = datetime.now()
                target_time_str = datetime.strptime(target_time, "%H:%M")
                target_time2_str = datetime.strptime(target_time2, "%H:%M")
                defference1 = abs(current_time - target_time_str)
                defference2 = abs(current_time - target_time2_str)

                if defference1 <= defference2:
                    # 指定時間まで待機してからスクレイピングを開始
                    wait_until_target_time(target_time2)
                    print(target_time2 + "まで待って処理を実行します。")
                    scrape_page(interval)
                elif defference2 <= defference1:
                    wait_until_target_time(target_time)
                    print(target_time + "まで待って処理を実行します。")
                    scrape_page(interval)

            elif target_time is not None:
                wait_until_target_time(target_time)
                print(target_time + "まで待って処理を実行します。")
                scrape_page(interval)
            elif target_time2 is not None:
                wait_until_target_time(target_time2)
                print(target_time2 + "まで待って処理を実行します。")
                scrape_page(interval)
            else:
                print("待機時間が入力されていなかったため実行します。")
                # カード名が入力されていれば、指定された時間にスクレイピング実行
                scrape_page(interval)
                # フォームから結果ページに遷移
                return render_result
        else:
            error = "カード名とメールアドレスを入力してもう一度やり直してください。"
            return render_template('index.html', error=error)
    
    if request.method == 'GET':
        return render_template('index.html')

@app.route('/back', methods=['GET'])
def back():
    """結果ページから戻るリンクを押したときにフォームページにリダイレクト"""
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)