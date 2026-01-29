import os
import glob

# パスを raw string または正確にエスケープして指定
path = r"C:\Users\gmkmini01\Desktop\徳重 颯人\ebay出品取り消しツール\app\auth\client_secret_190673900186-f3geid1aru6fggkndndnv4a6kb7ibrbv.apps.googleusercontent.com.json"

# ファイルパスの存在と種類を確認
print("os.path.exists():", os.path.exists(path))
print("os.path.isfile():", os.path.isfile(path))

# globを使用したファイル検索
glob_path = r"C:\Users\gmkmini01\Desktop\徳重 颯人\ebay出品取り消しツール\app\auth\client_secret_190673900186-f3geid1aru6fggkndndnv4a6kb7ibrbv.apps.googleusercontent.com.json"
found_files = glob.glob(glob_path)
print("Glob検索結果:")
for file in found_files:
    print(file)
    print("存在確認:", os.path.exists(file))
    print("ファイル確認:", os.path.isfile(file))