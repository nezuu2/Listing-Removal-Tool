from flask import Flask, render_template, request, redirect, url_for
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

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

def scrape_page():

    global soldout
    global target_item
    global stop_item

    stop_item = None

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

                options = webdriver.FirefoxOptions()
                options.add_argument("--headless")
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-gpu')
                options.add_argument('--ignore-certificate-errors')
                options.add_argument('--allow-running-insecure-content')
                options.add_argument('--disable-web-security')
                options.add_argument('--disable-desktop-notifications')
                options.add_argument("--disable-extensions")

                sent_merchandise = set()
                for url in urls:
                    # URLごとに状態をリセット
                    soldout = False
                    merchandise = None

                    try:
                        driver = webdriver.Firefox(options=options)
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

            # 全タブ処理が終わったらこの関数を抜ける（下の既存ロジックと衝突するのを避けるため）
            return
        except Exception as e:
            print(f"スプレッドシートのタブ取得エラー: {e}")
            traceback.print_exc()
            # フォールバック名
            sheat_name = "徳重"
    
    except Exception as e:
        print(e)
        sheat_name = "徳重"
    

    # 範囲指定（F列のみ）
    RANGE_NAME = f"{sheat_name}!F3:F1000"  # F列のデータを取得
    print(f"参照中のシート: {sheat_name} | 取得範囲: {RANGE_NAME}")

    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
        values = result.get('values', [])

        if not values:
            print('データが見つかりませんでした。')
        else:
            # F列の値のみ抽出
            urls = [row[0] for row in values if row and row[0].startswith('https://')]
            print(f'抽出されたURLリスト: {urls}')
            if len(urls) > 1:
                target_item = urls[1].replace("https://jp.mercari.com/item/", "")
                print(f"ターゲットアイテム: {target_item}")
            else:
                print("URLが見つからないか、リストに十分な要素がありません。データ内容: ")
                print(values)
                return

            # 結果を出力
            # for i, url in enumerate(urls):
            #     print(f"{i}: {url}")
            # 実行結果：i: url
            # 14: https://jp.mercari.com/item/m67472315685

            options = webdriver.FirefoxOptions()
            # options.add_argument("--user-data-dir=selenium-profile")
            options.add_argument("--headless")
            # 必須
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-gpu')
            # エラーの許容
            options.add_argument('--ignore-certificate-errors')
            options.add_argument('--allow-running-insecure-content')
            options.add_argument('--disable-web-security')
            # headlessでは不要そうな機能
            options.add_argument('--disable-desktop-notifications')
            options.add_argument("--disable-extensions")

            sent_merchandise = set()
            print("len: " + str(len(urls)))
            for i in range(len(urls)):

                response = requests.get(urls[i])
                soup = BeautifulSoup(response.content, 'html.parser')

                # chromedriverの設定とキーワード検索実行
                
                driver = webdriver.Firefox(options=options)
                
                driver.get(urls[i])
                print("現在のURLは" + urls[i] + "です。現在、" + str(i) + "回目です。")
                try:
                    element2 = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, "//button[contains(text(), '売り切れました')]"))
                    )
                    print("この商品は売り切れました。")
                    soldout = True
                    merchandise = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, "//h1[@class='heading__a7d91561 page__a7d91561']"))
                    )
                    merchandise = merchandise.text
                    print("merchandise: " + merchandise)
                except Exception as e:
                    print(f"エラーが発生しました: {str(e)}")
                finally:
                    driver.quit()
                if soldout:
                    if merchandise not in sent_merchandise:
                        # Chromeのスクレイピングに使うソース↓
                        # # chromedriverの設定とキーワード検索実行
                        # options = Options()
                        # # options.add_argument("--user-data-dir=selenium-profile")
                        # # options.add_argument('--headless')
                        # options.add_argument('--ignore-certificate-errors')
                        # options.add_argument('--ignore-ssl-errors')
                        # options.add_argument('--disable-web-security')
                        # options.add_experimental_option('excludeSwitches', ['enable-logging'])
                        # driver = webdriver.Chrome(options=options)

                        # cookies_file = 'morokoshi.pkl' # クッキーを保存するファイルの名前


                        # try:
                        #     # メール送信のセットアップ
                        #     yag = yagmail.SMTP('nezuu.mail2@gmail.com', 'lwjxsrmqjzxhsjhm')
                        
                        #     # メール送信
                        #     subject = merchandise.text + "が売り切れました!", "utf-8"
                        #     contents = merchandise.text + "が売り切れたことを、ここに報告致します。"
                        
                        #     yag.send(str(request.form.get('mail')), subject, contents)
                        #     print("メールが正常に送信されました")
                        
                        # except Exception as e:
                        #     print(f"エラー: {e}")
                        
                        # finally:
                        #     try:
                        #         yag.close()
                        #     except:
                        #         pass

                        # # 商品の在庫数をメールに送る処理をここにかく↓
                        # # メール送信者と受信者の情報を設定
                        # ## sender_email = "jannamailserver@gmail.com"
                        # ## receiver_email = mail_status
                        # ## password = "jybgimxpnhqurifn"
                        # gmail_smtp_ip = "142.250.142.109"
                        # socket.setdefaulttimeout(180)
                        # sender_email = "nezuu.mail2@gmail.com"
                        # receiver_email = str(request.form.get('mail'))
                        # print(str(request.form.get('mail')))
                        # password = "lwjxsrmqjzxhsjhm"

                        # # メールの内容を設定　：subject件名　body内容
                        # subject = merchandise.text + "が売り切れました!", "utf-8"
                        # body = merchandise.text + "が売り切れたことを、ここに報告致します。" #/n 価格は" + pricech_text + "です。
                        # ## Quest１．merchandise（商品名）と価格(pricech_text)をスクレイピングにて取得する。
                        # # MIMEText オブジェクトの作成
                        # msg = MIMEMultipart()
                        # msg['From'] = sender_email
                        # msg['To'] = receiver_email#送り先のメールアドレス
                        # msg['Subject'] = Header(subject, "utf-8")
                        # msg.attach(MIMEText(body, 'plain', "utf-8"))

                        # try:
                        #     # GmailのSMTPサーバーに接続
                        #     server = smtplib.SMTP('smtp.gmail.com', 587, timeout=180)
                        #     server.set_debuglevel(1)  # デバッグモードを有効化
                        #     server.starttls() #暗号化
                        #     server.ehlo()
                        #     server.login(sender_email, password)

                        #     # メールを送信
                        #     server.sendmail(sender_email, receiver_email, msg.as_string())
                        #     print("Email sent successfully")

                        # except Exception as e:
                        #     print(f"Error: {e}" + "メールの送信に失敗しました。")

                        # finally:
                        #     server.quit()

                        receiver_email = str(request.form.get('mail'))
                        # 先に定義されていない target_sku を参照しないようにし、
                        # send_notification_email を使って一箇所から送信する
                        success = send_notification_email(receiver_email, merchandise)
                        if success:
                            sent_merchandise.add(merchandise)
                    else:
                        print(f"{merchandise}はすでにメール送信済みです。")
                
                if soldout == True:

                    # Chromeのスクレイピングに使うソース↓
                    # # chromedriverの設定とキーワード検索実行
                    # options = Options()
                    # # options.add_argument("--user-data-dir=selenium-profile")
                    # # options.add_argument('--headless')
                    # options.add_argument('--ignore-certificate-errors')
                    # options.add_argument('--ignore-ssl-errors')
                    # options.add_argument('--disable-web-security')
                    # options.add_experimental_option('excludeSwitches', ['enable-logging'])
                    # driver = webdriver.Chrome(options=options)

                    # cookies_file = 'morokoshi.pkl' # クッキーを保存するファイルの名前


                    # try:
                    #     # メール送信のセットアップ
                    #     yag = yagmail.SMTP('nezuu.mail2@gmail.com', 'lwjxsrmqjzxhsjhm')
                        
                    #     # メール送信
                    #     subject = merchandise.text + "が売り切れました!", "utf-8"
                    #     contents = merchandise.text + "が売り切れたことを、ここに報告致します。"
                        
                    #     yag.send(str(request.form.get('mail')), subject, contents)
                    #     print("メールが正常に送信されました")
                        
                    # except Exception as e:
                    #     print(f"エラー: {e}")
                        
                    # finally:
                    #     try:
                    #         yag.close()
                    #     except:
                    #         pass

                    # # 商品の在庫数をメールに送る処理をここにかく↓
                    # # メール送信者と受信者の情報を設定
                    # ## sender_email = "jannamailserver@gmail.com"
                    # ## receiver_email = mail_status
                    # ## password = "jybgimxpnhqurifn"
                    # gmail_smtp_ip = "142.250.142.109"
                    # socket.setdefaulttimeout(180)
                    # sender_email = "nezuu.mail2@gmail.com"
                    # receiver_email = str(request.form.get('mail'))
                    # print(str(request.form.get('mail')))
                    # password = "lwjxsrmqjzxhsjhm"

                    # # メールの内容を設定　：subject件名　body内容
                    # subject = merchandise.text + "が売り切れました!", "utf-8"
                    # body = merchandise.text + "が売り切れたことを、ここに報告致します。" #/n 価格は" + pricech_text + "です。
                    # ## Quest１．merchandise（商品名）と価格(pricech_text)をスクレイピングにて取得する。
                    # # MIMEText オブジェクトの作成
                    # msg = MIMEMultipart()
                    # msg['From'] = sender_email
                    # msg['To'] = receiver_email#送り先のメールアドレス
                    # msg['Subject'] = Header(subject, "utf-8")
                    # msg.attach(MIMEText(body, 'plain', "utf-8"))

                    # try:
                    #     # GmailのSMTPサーバーに接続
                    #     server = smtplib.SMTP('smtp.gmail.com', 587, timeout=180)
                    #     server.set_debuglevel(1)  # デバッグモードを有効化
                    #     server.starttls() #暗号化
                    #     server.ehlo()
                    #     server.login(sender_email, password)

                    #     # メールを送信
                    #     server.sendmail(sender_email, receiver_email, msg.as_string())
                    #     print("Email sent successfully")

                    # except Exception as e:
                    #     print(f"Error: {e}" + "メールの送信に失敗しました。")

                    # finally:
                    #     server.quit()

                    receiver_email = str(request.form.get('mail'))
                    success = send_notification_email(receiver_email, merchandise)
                    if success:
                        sent_merchandise.add(merchandise)
                
                    try:
                        # # ファイルが存在しなかったら作成する
                        # if not Path(os.path.join(current_dir, "appid.txt")).exists():
                        #     os.path.join(current_dir, "appid.txt").touch()
                            
                        #     path_w = os.path.join(current_dir, "appid.txt")
                        #     with open(path_w, mode='w') as f:
                        #         f.write('MANABUKU-myapp-PRD-dfe4c757a-0849e252')
                        # if not Path(os.path.join(current_dir, "devid.txt")).exists():
                        #     os.path.join(current_dir, "devid.txt").touch()
                            
                        #     path_w = os.path.join(current_dir, "devid.txt")
                        #     with open(path_w, mode='w') as f:
                        #      f.write('37712345-e36e-4afb-80d9-3f8273f52f08')
                        # if not Path(os.path.join(current_dir, "certid.txt")).exists():
                        #     os.path.join(current_dir, "certid.txt").touch()
                            
                        #     path_w = os.path.join(current_dir, "certid.txt")
                        #     with open(path_w, mode='w') as f:
                        #      f.write('PRD-fe4c757a9132-1139-4a19-a2ca-4325')
                        # if not Path(os.path.join(current_dir, "token.txt")).exists():
                        #     os.path.join(current_dir, "token.txt").touch()
                            
                        #     path_w = os.path.join(current_dir, "token.txt")
                        #     with open(path_w, mode='w') as f:
                        #       f.write('v^1.1#i^1#r^1#f^0#p^3#I^3#t^Ul4xMF85OjFGMjJEQzM2MTQ2QzlCRDYxRjkxQjhBNDhBQzY2MTVFXzBfMSNFXjI2MA==')
                              
                        appid='MANABUKU-myapp-PRD-dfe4c757a-0849e252'
                        devid='37712345-e36e-4afb-80d9-3f8273f52f08'
                        certid='PRD-fe4c757a9132-1139-4a19-a2ca-4325'
                        token='v^1.1#i^1#r^1#f^0#p^3#I^3#t^Ul4xMF85OjFGMjJEQzM2MTQ2QzlCRDYxRjkxQjhBNDhBQzY2MTVFXzBfMSNFXjI2MA=='

                        # ユーザー情報を取得するAPI
                        try:
                            api = Trading(appid=appid, devid=devid, certid=certid, token=token, config_file=None)
                            response = api.execute('GetUser', {})
                            response = response.dict()
                            print(response.keys())
                            print(response.reply)
                            print(response['Timestamp'])
                            print(response['Ack'])
                            print(response['Version'])
                            print(response['Build'])
                            print(response['User'])
                        except ConnectionError as e:
                            print(e.response.dict())

                        # 検索したいSKU
                        target_sku = "#me_" + target_item
                        #target_sku = "#me_m89236475742"

                        # 過去30日間の出品を取得
                        start_time = datetime.now() - timedelta(days=30)
                        end_time = datetime.now()

                        try:
                            # シート名をそのまま使用
                            RANGE_NAME = f"{sheat_name}!E2:E1000"
                            print(f"使用する範囲: {RANGE_NAME}")  # デバッグ用ログ

                            result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
                            values = result.get('values', [])
                        except Exception as e:
                            print(f"Google Sheets APIリクエストでエラーが発生しました: {e}")
                            traceback.print_exc()
                            return
                        
                        if not values:
                            print("スプレッドシートにデータがありません。")
                        return

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
                        except ConnectionError as e:
                            print(f"eBay APIリクエストでエラーが発生しました: {e}")
                            traceback.print_exc()
                            return
                            
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
                                print("出品を取り下げる商品が見つかりませんでした。")
                            
                        except Exception as e:
                            print(f"エラーが発生しました: {e}")
                    except Exception as e:
                        print(f"エラーが発生しました: {e}")
                        #--------------------------------------
                        try:
                            if stop_item is not None:
                                # 取り下げ前に出品状態を確認するヘルパー
                                def is_item_active(api_obj, item_id):
                                    try:
                                        resp = api_obj.execute('GetItem', {'ItemID': item_id})
                                        # resp.reply.Item へのアクセスはAPIレスポンスに依存するため安全に取得
                                        reply = getattr(resp, 'reply', None)
                                        item = None
                                        if reply is not None and hasattr(reply, 'Item'):
                                            item = reply.Item
                                        # Try multiple attributes to determine if listing ended
                                        try:
                                            # ListingDetails.EndTime が存在すれば解析して過去なら ended
                                            end_time_str = getattr(getattr(item, 'ListingDetails', None), 'EndTime', None)
                                            if end_time_str:
                                                try:
                                                    # eBay では ISO 8601 の UTC 文字列が返ることが多い
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

                                        try:
                                            selling_status = getattr(item, 'SellingStatus', None)
                                            listing_status = getattr(selling_status, 'ListingStatus', None)
                                            if listing_status and str(listing_status).lower() == 'ended':
                                                return False
                                        except Exception:
                                            pass

                                        # デフォルトはアクティブと仮定
                                        return True
                                    except Exception as e:
                                        print(f"GetItem チェックでエラーが発生しました: {e}")
                                        return False

                                # 出品がまだアクティブなら取り下げを実行
                                if is_item_active(api, stop_item):
                                    response = api.execute('EndFixedPriceItem', {
                                        'ItemID': stop_item,
                                        'EndingReason': 'NotAvailable'  # 取り下げ理由
                                    })
                                    print("出品の取り下げが成功しました。")
                                    print(f"Status: {response.reply.Ack}")
                                else:
                                    print(f"Item {stop_item} は既に終了しているため、取り下げをスキップします。")

                                ##ログを保存するソース↓
                                # 既存のロガー（ルートロガーなど）を取得
                                logger = logging.getLogger()  # ルートロガーを取得する場合

                                # 既存のハンドラを全て削除（オプション）
                                for handler in logger.handlers[:]:
                                    logger.removeHandler(handler)

                                # ファイルハンドラを追加
                                file_handler = logging.FileHandler('output.log')
                                formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                                file_handler.setFormatter(formatter)
                                logger.addHandler(file_handler)

                                # 標準出力にも残したい場合はStreamHandlerも追加
                                stream_handler = logging.StreamHandler(sys.stdout)
                                stream_handler.setFormatter(formatter)
                                logger.addHandler(stream_handler)

                            else:
                              print("取り下げる商品が見つかりませんでした。")
                            
                        except Exception as e:
                            print(f"エラー: {type(e).__name__}")
                            print("詳細: ")
                            traceback.print_exc()  # エラーのトレースバック情報を出力

                            ##ログを保存するソース↓
                            # 既存のロガー（ルートロガーなど）を取得
                            logger = logging.getLogger()  # ルートロガーを取得する場合

                            # 既存のハンドラを全て削除（オプション）
                            for handler in logger.handlers[:]:
                                logger.removeHandler(handler)

                            # ファイルハンドラを追加
                            file_handler = logging.FileHandler('output.log')
                            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                            file_handler.setFormatter(formatter)
                            logger.addHandler(file_handler)

                            # 標準出力にも残したい場合はStreamHandlerも追加
                            stream_handler = logging.StreamHandler(sys.stdout)
                            stream_handler.setFormatter(formatter)
                            logger.addHandler(stream_handler)
                    except Exception as e:
                        print(f"エラー: {type(e).__name__}")
                        print("詳細: ")
                        traceback.print_exc()  # エラーのトレースバック情報を出力

    except Exception as e:
        print(f"エラー: {type(e).__name__}")
        print("詳細: ")
        traceback.print_exc()  # エラーのトレースバック情報を出力


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

        if mail_status:

            if target_time == "":
                target_time = None
            elif target_time2 == "":
                target_time2 = None

            # target_timeに数字が入っていたら実行する
            if target_time != None and target_time2 != None and target_time or target_time2 != "":
                current_time = datetime.now()
                target_time_str = datetime.strptime(target_time, "%H:%M")
                target_time2_str = datetime.strptime(target_time2, "%H:%M")
                defference1 = abs(current_time - target_time_str)
                defference2 = abs(current_time - target_time2_str)

                if defference1 <= defference2:
                    # 指定時間まで待機してからスクレイピングを開始
                    wait_until_target_time(target_time2)
                    print(target_time2 + "まで待って処理を実行します。")
                elif defference2 <= defference1:
                    wait_until_target_time(target_time)
                    print(target_time + "まで待って処理を実行します。")

            elif target_time != None and target_time2 == "" or None:
                wait_until_target_time(target_time)
                print(target_time + "まで待って処理を実行します。")
            elif target_time2 != None and target_time == "" or None:
                wait_until_target_time(target_time2)
                print(target_time2 + "まで待って処理を実行します。")
            else:
                print("待機時間が入力されていなかったため実行します。")
                # カード名が入力されていれば、指定された時間にスクレイピング実行
                scrape_page()
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