# Dosya Adı: app.py
# GÖREV: S3'ten 'nba_analiz.db', 'games_today.json' VE 'nba-injury-report.csv'
# dosyalarını indirir. (Log silme fonksiyonları eklendi)

from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify 
import pandas as pd
import numpy as np 
from datetime import datetime, timedelta
import traceback 
import json
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from functools import wraps
import time
import urllib.request 
import threading 

import analysis_engine 

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"Uygulama Ana Dizini (BASE_DIR): {BASE_DIR}")

# ======================================================
# === UYGULAMA YAPILANDIRMASI (CONFIG) ===
# ======================================================

load_dotenv()
DB_FILE_URL = os.getenv("DB_FILE_URL")

# --- Fikstür ve Sakatlık URL'lerini otomatik oluştur ---
GAMES_TODAY_URL = ""
INJURY_FILE_URL = "" # <--- YENİ
if DB_FILE_URL:
    GAMES_TODAY_URL = DB_FILE_URL.replace("nba_analiz.db", "games_today.json")
    INJURY_FILE_URL = DB_FILE_URL.replace("nba_analiz.db", "nba-injury-report.csv") # <--- YENİ
    print(f"Fikstür URL'si ayarlandı: {GAMES_TODAY_URL}")
    print(f"Sakatlık URL'si ayarlandı: {INJURY_FILE_URL}") # <--- YENİ
else:
    print("KRİTİK HATA: .env dosyasında DB_FILE_URL (S3 dosya adresi) bulunamadı.")
# --- BİTTİ ---


RENDER_DB_PATH = "/tmp/nba_analiz.db" 
RENDER_GAMES_TODAY_PATH = "/tmp/games_today.json"
RENDER_INJURY_PATH = "/tmp/nba-injury-report.csv" # <--- YENİ
engine = None 

# --- Analiz Ayarları ---
ANALYSIS_RANGE = 3.0 
MINIMUM_PATTERN_PROBABILITY = 75.0 
TOP_N_PLAYERS_PER_TEAM = 5 
CURRENT_SEASON_START_DATE = '2025-09-01'

# --- Dosya Yolları (Kalıcı Disk için Güncellendi) ---
# Render'da oluşturduğumuz diskin yolu
DATA_DIR = "/var/data/projem"

# Bu klasörün var olduğundan emin ol (Render'da ilk çalıştırmada)
try:
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        print(f"Başarılı: Kalıcı disk dizini '{DATA_DIR}' oluşturuldu.")
except Exception as e:
    print(f"UYARI: Kalıcı disk dizini '{DATA_DIR}' oluşturulamadı: {e}")
    # Hata olursa, en azından mevcut (geçici) dizine yazsın
    DATA_DIR = "." 

# Dosya yollarını bu kalıcı dizine yönlendir
CACHE_FILE = os.path.join(DATA_DIR, "barem_cache.json")
LOG_FILE = os.path.join(DATA_DIR, "analysis_log.json")
# --- GÜNCELLEME BİTTİ ---


# --- Global Değişkenler ---
DATA_CACHE = {"data_last_loaded": None}
df_oyuncu_mac = None
df_oyuncu_sezon = None
df_takim_mac = None
df_games_today = pd.DataFrame() 
df_injury_report = pd.DataFrame() # <--- YENİ: Sakatlıklar için boş DataFrame
ALL_TEAMS_LIST = []
ALL_PLAYERS_LIST = []
nba_team_id_to_abbr = {}
nba_abbr_to_id = {}
cached_barems = {}
cached_player_list_key = ""
analysis_log = {}
DATA_LOCK = threading.Lock() 


# ======================================================
# === VERİ YÜKLEME (S3'TEN İNDİRME) ===
# ======================================================

def load_data_from_s3():
    """
    S3'ten 'nba_analiz.db', 'games_today.json' VE 'nba-injury-report.csv'
    dosyalarını indirir ve global DataFrame'leri doldurur.
    """
    global df_oyuncu_mac, df_oyuncu_sezon, df_takim_mac, ALL_TEAMS_LIST, ALL_PLAYERS_LIST
    global nba_team_id_to_abbr, nba_abbr_to_id, engine, DATA_CACHE, df_games_today
    global df_injury_report # <--- YENİ
    
    print("Veri kilidi alınıyor (load_data_from_s3)...") 
    with DATA_LOCK: 
        print("Veri yükleme fonksiyonu (load_data_from_s3) başladı...")
        start_time = time.time()
        
        if not DB_FILE_URL or not GAMES_TODAY_URL or not INJURY_FILE_URL:
            print("HATA: DB_FILE_URL, GAMES_TODAY_URL veya INJURY_FILE_URL ayarlanmamış, veri çekilemiyor.")
            return False
            
        try:
            # === BÖLÜM 1: Veritabanını İndir (nba_analiz.db) ===
            print(f"S3'ten veritabanı indiriliyor: {DB_FILE_URL}")
            urllib.request.urlretrieve(DB_FILE_URL, RENDER_DB_PATH)
            print(f"Dosya başarıyla '{RENDER_DB_PATH}' konumuna indirildi.")

            engine = create_engine(f'sqlite:///{RENDER_DB_PATH}')
            print("Geçici SQLite veritabanına bağlanıldı.")

            # Tabloları Oku (Geçmiş Veriler)
            print("  Okunuyor: oyuncu_mac_performanslari")
            df_oyuncu_mac = pd.read_sql_query("SELECT * FROM oyuncu_mac_performanslari", con=engine)
            
            print("  Okunuyor: oyuncu_sezon_istatistikleri")
            df_oyuncu_sezon = pd.read_sql_query("SELECT * FROM oyuncu_sezon_istatistikleri", con=engine)
            
            print("  Okunuyor: maclar (Geçmiş Maçlar)")
            df_takim_mac = pd.read_sql_query("SELECT * FROM maclar", con=engine)
            
            print("Veritabanından okuma tamamlandı. Veri tipleri dönüştürülüyor...")
            
            # Tipleri dönüştür...
            cols_mac = ['PTS', 'FGA', 'FGM', 'FG_PCT', 'FTA', 'FTM', 'FT_PCT', 'REB', 'AST']
            for col in cols_mac:
                if col in df_oyuncu_mac.columns:
                    df_oyuncu_mac[col] = pd.to_numeric(df_oyuncu_mac[col], errors='coerce').fillna(0)
            cols_sezon = ['GP', 'MIN', 'PTS', 'FGA', 'FGM', 'FTA', 'FTM', 'REB', 'AST']
            for col in cols_sezon:
                 if col in df_oyuncu_sezon.columns:
                    df_oyuncu_sezon[col] = pd.to_numeric(df_oyuncu_sezon[col], errors='coerce').fillna(0)
            df_takim_mac['PTS'] = pd.to_numeric(df_takim_mac['PTS'], errors='coerce').fillna(0)
            df_oyuncu_mac['GAME_DATE'] = pd.to_datetime(df_oyuncu_mac['GAME_DATE'], errors='coerce')
            df_takim_mac['GAME_DATE'] = pd.to_datetime(df_takim_mac['GAME_DATE'], errors='coerce')
            df_oyuncu_mac = df_oyuncu_mac.dropna(subset=['GAME_DATE', 'PLAYER_ID', 'GAME_ID'])
            df_takim_mac = df_takim_mac.dropna(subset=['GAME_DATE']) 

            # Listeleri Oluştur
            ALL_PLAYERS_LIST = sorted(df_oyuncu_mac['PLAYER_NAME'].unique())
            ALL_TEAMS_LIST = sorted(df_takim_mac['TEAM_NAME'].unique())
            
            print(f"Başarılı: {len(ALL_PLAYERS_LIST)} oyuncu, {len(ALL_TEAMS_LIST)} takım belleğe yüklendi.")
            
            # === BÖLÜM 2: Fikstürü İndir (games_today.json) ===
            print(f"S3'ten fikstür indiriliyor: {GAMES_TODAY_URL}")
            urllib.request.urlretrieve(GAMES_TODAY_URL, RENDER_GAMES_TODAY_PATH)
            print(f"Fikstür başarıyla '{RENDER_GAMES_TODAY_PATH}' konumuna indirildi.")
            
            df_games_today = pd.read_json(RENDER_GAMES_TODAY_PATH)
            
            if df_games_today.empty:
                print("UYARI: Fikstür dosyası bulundu ancak içi boş.")
            else:
                print(f"Fikstür başarıyla belleğe yüklendi ({len(df_games_today)} maç).")

            # === BÖLÜM 3: Sakatlık Raporunu İndir (nba-injury-report.csv) ===
            try:
                print(f"S3'ten sakatlık raporu indiriliyor: {INJURY_FILE_URL}")
                urllib.request.urlretrieve(INJURY_FILE_URL, RENDER_INJURY_PATH)
                df_injury_report = pd.read_csv(RENDER_INJURY_PATH)
                print(f"Sakatlık raporu başarıyla belleğe yüklendi ({len(df_injury_report)} oyuncu).")
            except FileNotFoundError:
                print("UYARI: S3'te 'nba-injury-report.csv' dosyası bulunamadı.")
                print("   -> (Lokalden yüklemeyi unutmuş olabilirsiniz, analiz sakatlık filtresi olmadan devam edecek)")
                df_injury_report = pd.DataFrame(columns=['Player']) # Boş bir DF oluştur
            except Exception as e_inj:
                print(f"UYARI: Sakatlık raporu indirilirken/okunurken hata: {e_inj}")
                df_injury_report = pd.DataFrame(columns=['Player']) # Boş bir DF oluştur
            # === BÖLÜM 3 BİTTİ ===

            DATA_CACHE["data_last_loaded"] = time.ctime()
            
        except Exception as e:
            print(f"KRİTİK HATA: S3'ten veri yüklenirken hata oluştu: {e}")
            print(traceback.format_exc())
            print("Veri kilidi serbest bırakıldı (load_data_from_s3 - HATA).")
            return False 
        
        # NBA API Haritasını Yükle
        try:
            from nba_api.stats.static import teams as nba_static_teams
            nba_teams_all = nba_static_teams.get_teams()
            nba_team_id_to_abbr = {team['id']: team['abbreviation'] for team in nba_teams_all}
            nba_abbr_to_id = {team['abbreviation']: team['id'] for team in nba_teams_all}
        except Exception:
             print("UYARI: nba-api 'get_teams' çağrısı başarısız. Bellekten yedek harita oluşturuluyor.")
             try:
                 team_data = df_oyuncu_sezon[['TEAM_ID', 'TEAM_ABBREVIATION']].drop_duplicates()
                 nba_team_id_to_abbr = pd.Series(
                     team_data['TEAM_ABBREVIATION'].values, 
                     index=team_data['TEAM_ID']
                 ).to_dict()
                 nba_abbr_to_id = pd.Series(
                     team_data['TEAM_ID'].values, 
                     index=team_data['TEAM_ABBREVIATION']
                 ).to_dict()
             except Exception as e_map:
                 print(f"HATA: Yedek harita oluşturulamadı: {e_map}")
                 nba_team_id_to_abbr = {}
                 nba_abbr_to_id = {}
             
        end_time = time.time()
        print(f"--- VERİ BAŞARIYLA YÜKLENDİ ({end_time - start_time:.2f} saniye) ---")
        print("Veri kilidi serbest bırakıldı (load_data_from_s3 - Başarılı).")
        return True 

# ======================================================
# === HAFIZA (CACHE & LOG) YÖNETİMİ ===
# ======================================================

def load_cache():
    global cached_barems, cached_player_list_key
    if not os.path.exists(CACHE_FILE):
        return 
    try:
        with open(CACHE_FILE, 'r') as f:
            cache_data = json.load(f)
            cached_barems = cache_data.get('barems', {})
            cached_player_list_key = cache_data.get('key', "")
            print(f"BAŞARILI: '{CACHE_FILE}' dosyasından barem hafızası yüklendi.")
    except Exception as e:
        print(f"HATA: '{CACHE_FILE}' okunurken hata (dosya bozuk olabilir): {e}")
        cached_barems = {}
        cached_player_list_key = ""

def save_cache():
    try:
        with open(CACHE_FILE, 'w') as f:
            cache_data = {
                'key': cached_player_list_key, 
                'barems': cached_barems
            }
            json.dump(cache_data, f, indent=4)
            print(f"BAŞARILI: Barem hafızası '{CACHE_FILE}' dosyasına kaydedildi.")
    except Exception as e:
        print(f"HATA: Barem hafızası '{CACHE_FILE}' dosyasına kaydedilemedi: {e}")

def load_log():
    global analysis_log
    if not os.path.exists(LOG_FILE):
        return 
    try:
        with open(LOG_FILE, 'r') as f:
            analysis_log = json.load(f)
            print(f"BAŞARILI: '{LOG_FILE}' dosyasından analiz logları yüklendi.")
    except Exception as e:
        print(f"HATA: '{LOG_FILE}' okunurken hata (dosya bozuk olabilir): {e}")
        analysis_log = {}

def save_log():
    try:
        with open(LOG_FILE, 'w') as f:
            json.dump(analysis_log, f, indent=4)
            print(f"BAŞARILI: Analiz logları '{LOG_FILE}' dosyasına kaydedildi.")
    except Exception as e:
        print(f"HATA: Analiz logları '{LOG_FILE}' dosyasına kaydedilemedi: {e}")

def clean_data_for_json(data_list): 
    cleaned_list = []
    if not isinstance(data_list, list):
        return []
    for item_dict in data_list:
        if not isinstance(item_dict, dict):
            cleaned_list.append(item_dict)
            continue
        cleaned_item = {}
        for key, value in item_dict.items():
            if pd.isna(value):
                cleaned_item[key] = None
            elif isinstance(value, np.integer): 
                cleaned_item[key] = int(value)
            elif isinstance(value, np.floating): 
                cleaned_item[key] = float(value)
            elif isinstance(value, np.bool_): 
                cleaned_item[key] = bool(value)
            else:
                cleaned_item[key] = value 
        cleaned_list.append(cleaned_item)
    return cleaned_list

# --- Flask Uygulaması ---
app = Flask(__name__)
app.secret_key = 'sizin-cok-gizli-anahtariniz-12345' 

# ======================================================
# === KULLANICI GİRİŞ (LOGIN) SİSTEMİ ===
# ======================================================
ADMIN_USERNAME = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASS', '12345')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('route_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function 

def api_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return jsonify({"error": "Yetkisiz erişim. Lütfen tekrar giriş yapın.", "data": []}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def route_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            print("Giriş başarılı!")
            next_url = request.form.get('next')
            if next_url:
                return redirect(next_url)
            return redirect(url_for('route_index'))
        else:
            error = 'Geçersiz Kullanıcı Adı veya Şifre'
            print("Giriş denemesi başarısız.")
    
    return f'''
        <form method="post">
            <h2>Giriş Yap</h2>
            Kullanıcı Adı: <input type="text" name="username"><br>
            Şifre: <input type="password" name="password"><br>
            <input type="hidden" name="next" value="{request.args.get('next', '')}">
            <input type="submit" value="Giriş">
            {f'<p style="color:red;">{error}</p>' if error else ''}
        </form>
    '''

@app.route('/logout')
def route_logout():
    session.pop('logged_in', None)
    return redirect(url_for('route_login'))

# ======================================================
# === WEB SAYFASI ROTALARI (SAYFA ADRESLERİ) ===
# ======================================================

@app.route('/')
@login_required
def route_index():
    return render_template("index.html", 
                           sonuclar=None, 
                           top_2_picks=[], 
                           all_results_ready=False)

@app.route('/oyuncu')
@login_required
def route_oyuncu():
    return render_template("oyuncu.html", players=ALL_PLAYERS_LIST, report_string=None)

@app.route('/takim')
@login_required
def route_takim():
    return render_template("takim.html", teams=ALL_TEAMS_LIST, report_string=None)

@app.route('/backtest')
@login_required
def route_backtest():
    sorted_dates = sorted(analysis_log.keys(), reverse=True)
    return render_template("backtest.html", log_dates=sorted_dates)

@app.route('/veri-guncelle') 
@login_required
def route_veri_guncelle():
    last_load = DATA_CACHE.get("data_last_loaded", "Veri henüz yüklenmedi")
    return render_template("veri_guncelle.html", 
                           message=f"Son Veri Yükleme Zamanı: {last_load}",
                           is_running=False) 

@app.route('/refresh')
@login_required
def route_refresh_data():
    print("Manuel veri yenileme (S3'ten indirme) tetiklendi...")
    success = load_data_from_s3()
    if success:
        print("Veri başarıyla S3'ten yeniden indirildi.")
    else:
        print("Veri yüklenirken HATA oluştu.")
    return redirect(url_for('route_veri_guncelle'))


@app.route('/browse-data') 
@app.route('/browse-data/<string:file>') 
@login_required
def route_browse_data(file=None):
    file_key = request.args.get('file') or file
    column_names = []
    file_name = ""
    data_shape = (0, 0)
    
    print("Veri kilidi alınıyor (Veri Gözat - Kabuk)...")
    with DATA_LOCK:
        print("Veri kilidi alındı (Veri Gözat - Kabuk).")
        target_df = None
        if file_key == 'oyuncu_mac' and df_oyuncu_mac is not None:
            file_name = "oyuncu_mac_performanslari.csv"
            target_df = df_oyuncu_mac
        elif file_key == 'oyuncu_sezon' and df_oyuncu_sezon is not None:
            file_name = "oyuncu_sezon_istatistikleri.csv"
            target_df = df_oyuncu_sezon
        elif file_key == 'takim_mac' and df_takim_mac is not None:
            file_name = "maclar.csv (Geçmiş)"
            target_df = df_takim_mac
        elif file_key == 'fikstur' and not df_games_today.empty:
            file_name = "games_today.json (Fikstür)"
            target_df = df_games_today
        elif file_key == 'sakatlik' and not df_injury_report.empty: 
            file_name = "nba-injury-report.csv (Sakatlık)"
            target_df = df_injury_report
            
        if target_df is not None:
            data_shape = target_df.shape
            column_names = list(target_df.columns)
    print("Veri kilidi serbest bırakıldı (Veri Gözat - Kabuk).")
            
    column_names_json = json.dumps(column_names)
    datatable_lang_url = "https://cdn.datatables.net/plug-ins/2.0.8/i18n/tr.json"
    
    return render_template(
        "browse_data.html",
        file_name=file_name,
        data_shape=data_shape,
        column_names=column_names,
        column_names_json=column_names_json,
        current_file_key=file_key,
        datatable_lang_url=datatable_lang_url 
    )


@app.route('/api/get_data/<string:file_key>')
@api_login_required 
def route_get_data(file_key):
    print(f"API verisi talep edildi: {file_key}")
    print("Veri kilidi alınıyor (API)...")
    with DATA_LOCK:
        print("Veri kilidi alındı (API).")
        target_df = None
        if file_key == 'oyuncu_mac' and df_oyuncu_mac is not None:
            target_df = df_oyuncu_mac
        elif file_key == 'oyuncu_sezon' and df_oyuncu_sezon is not None:
            target_df = df_oyuncu_sezon
        elif file_key == 'takim_mac' and df_takim_mac is not None:
            target_df = df_takim_mac
        elif file_key == 'fikstur' and not df_games_today.empty:
            target_df = df_games_today
        elif file_key == 'sakatlik' and not df_injury_report.empty:
            target_df = df_injury_report
        else:
            print("Veri kilidi serbest bırakıldı (API - Hata).")
            return jsonify({"error": "Geçersiz dosya anahtarı veya veri yüklenmemiş", "data": []}), 404
        try:
            temp_df = target_df.copy()
            if 'GAME_DATE' in temp_df.columns:
                temp_df['GAME_DATE'] = temp_df['GAME_DATE'].dt.date.astype(str).replace('NaT', None)
            if 'GAME_DATE_EST' in temp_df.columns:
                temp_df['GAME_DATE_EST'] = pd.to_datetime(temp_df['GAME_DATE_EST']).dt.date.astype(str).replace('NaT', None)
                
            data_records = temp_df.to_dict('records')
            data_records_clean = clean_data_for_json(data_records)
            print("Veri kilidi serbest bırakıldı (API - Başarılı).")
            return jsonify(data=data_records_clean)
        except Exception as e:
            print(f"KRİTİK HATA (route_get_data): JSON dönüşümü başarısız! {e}")
            print(traceback.format_exc())
            print("Veri kilidi serbest bırakıldı (API - Hata).")
            return jsonify({"error": str(e), "data": []}), 500


@app.route('/all-results')
@login_required
def route_all_results():
    sorted_results = session.get('last_full_analysis_results', [])
    if not sorted_results:
        return redirect(url_for('route_index'))
    main_screen_picks = session.get('last_diverse_recommendations', [])
    top_4_diverse = []
    seen_games_top4 = set()
    other_results = []
    
    for aday in sorted_results:
        game_id = aday.get('game_id', None)
        if aday['pts_prob'] >= MINIMUM_PATTERN_PROBABILITY: 
            if len(top_4_diverse) < 4: 
                if game_id not in seen_games_top4:
                    top_4_diverse.append(aday)
                    seen_games_top4.add(game_id)
                else:
                    other_results.append(aday) 
            else:
                other_results.append(aday) 
        else:
            other_results.append(aday)
            
    return render_template("all_results.html", 
                           top_4_diverse=top_4_diverse,
                           other_results=other_results,
                           main_screen_picks=main_screen_picks,
                           MINIMUM_PATTERN_PROBABILITY=MINIMUM_PATTERN_PROBABILITY
                           )

# ======================================================
# === ANALİZ TETİKLEYİCİLERİ ===
# ======================================================

@app.route('/takim-analizi', methods=['POST'])
@login_required
def handle_team_analysis():
    with DATA_LOCK: 
        try:
            team_name = request.form.get('team_name')
            threshold_str = request.form.get('threshold', '105.5')
            threshold = float(threshold_str)
            print(f"Takım analizi talebi alındı: {team_name} @ {threshold}")

            report_string = analysis_engine.analyze_team_logic(
                team_name=team_name,
                threshold=threshold,
                df_takim_mac=df_takim_mac # Bu, geçmiş veritabanını kullanır (doğru)
            )
            return render_template(
                "takim.html", 
                teams=ALL_TEAMS_LIST,      
                selected_team=team_name,   
                selected_threshold=threshold_str, 
                report_string=report_string 
            )
        except Exception as e:
            error_report = f"KRİTİK HATA:\n{e}\n\n{traceback.format_exc()}"
            return render_template(
                "takim.html", 
                teams=ALL_TEAMS_LIST, 
                report_string=error_report
            )

@app.route('/oyuncu-analizi', methods=['POST'])
@login_required
def handle_player_analysis():
    with DATA_LOCK: 
        try:
            player_name = request.form.get('player_name')
            middle_barem_str = request.form.get('middle_barem', '18.5')
            middle_barem = float(middle_barem_str)
            print(f"Oyuncu (Aralık) analizi talebi alındı: {player_name} @ {middle_barem}")

            report_string, analysis_results = analysis_engine.analyze_player_logic(
                player_name=player_name,
                middle_barem=middle_barem,
                df_oyuncu_mac=df_oyuncu_mac,
                df_oyuncu_sezon=df_oyuncu_sezon,
                ANALYSIS_RANGE=ANALYSIS_RANGE
            )
            return render_template(
                "oyuncu.html", 
                players=ALL_PLAYERS_LIST,      
                selected_player=player_name,   
                selected_barem=middle_barem_str, 
                report_string=report_string 
            )
        except Exception as e:
            error_report = f"KRİTİK HATA:\n{e}\n\n{traceback.format_exc()}"
            return render_template(
                "oyuncu.html", 
                players=ALL_PLAYERS_LIST, 
                report_string=error_report
            )

# ======================================================
# === HİBRİT ANALİZ TETİKLEYİCİLERİ ===
# ======================================================

@app.route('/get-players')
@login_required
def handle_get_players():
    """
    'OYUNCU LİSTESİ AL' butonu tıklandığında çalışır.
    Fikstürü (df_games_today) VE Sakatlıkları (df_injury_report) 'analysis_engine'e gönderir.
    """
    global cached_barems, cached_player_list_key, nba_team_id_to_abbr, nba_abbr_to_id
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    report_lines = []
    top_players_final = None
    grouped_players = {}
    
    with DATA_LOCK: 
        print("Oyuncu listesi alma talebi alındı...")
        
        # --- DEĞİŞİKLİK BURADA: 'df_injury_report' (Sakatlık) parametresi eklendi ---
        try:
            (report_lines, 
             top_players_final, 
             today_str, 
             current_season_players_df, 
             csv_inactive_player_names) = analysis_engine.get_players_for_hybrid_analysis(
                 df_games_today=df_games_today, # <--- Fikstür
                 df_oyuncu_mac=df_oyuncu_mac,
                 df_oyuncu_sezon=df_oyuncu_sezon,
                 nba_team_id_to_abbr=nba_team_id_to_abbr,
                 df_injury_report=df_injury_report # <--- YENİ
             )
        # --- DEĞİŞİKLİK BİTTİ ---
        
        except Exception as e:
            error_report = f"KRİTİK HATA (analysis_engine): {e}\n\n{traceback.format_exc()}"
            print(error_report)
            return render_template("index.html", 
                                   sonuclar=error_report, 
                                   top_2_picks=[], 
                                   all_results_ready=False)

        if top_players_final is None:
            sonuclar = "\n".join(report_lines)
            return render_template("index.html", 
                                   sonuclar=sonuclar, 
                                   top_2_picks=[], 
                                   all_results_ready=False)

        player_names_for_popup = sorted(top_players_final['PLAYER_NAME'].tolist())
        current_player_list_key = "-".join(player_names_for_popup)
        
        if current_player_list_key != cached_player_list_key:
            report_lines.append("Yeni oyuncu listesi algılandı. Barem hafázası sıfırlanıyor...")
            cached_barems = {} 
            cached_player_list_key = current_player_list_key 
        else:
            report_lines.append("Hafızadaki baremler (cache) kullanılacak.")
        
        if top_players_final is not None:
            # --- GÜNCELLEME: Gruplama anahtarı 'MATCHUP' ---
            for game_id, group_df in top_players_final.groupby('GAME_ID', sort=False):
                # İlk oyuncudan ev sahibi ve deplasman isimlerini al
                home_team = group_df.iloc[0].get('HOME_TEAM', 'Ev Sahibi')
                away_team = group_df.iloc[0].get('AWAY_TEAM', 'Deplasman')
                matchup_name = f"{away_team} @ {home_team}" # Standart format
                
                grouped_players[matchup_name] = group_df.to_dict('records')
        
        return render_template("index.html", 
                               sonuclar="\n".join(report_lines),
                               players_to_analyze_grouped=grouped_players,
                               cached_barems=cached_barems, 
                               today_str=today_str,
                               all_results_ready=False)

@app.route('/run-analysis', methods=['POST'])
@login_required
def handle_run_analysis():
    global cached_barems, analysis_log
    
    with DATA_LOCK: 
        print("Tam analiz talebi alındı...")
        baremler = [] 
        form_data = request.form.to_dict()
        today_str = form_data.pop('today_str', datetime.now().strftime('%Y-%m-%d'))
        barem_dict = {}
        for key, value in form_data.items():
            if not value: 
                continue
            try:
                player_name = key.replace("barem_", "") 
                barem_dict[player_name] = float(value)
                baremler.append((player_name, float(value)))
            except:
                pass 
        cached_barems.update(barem_dict)
        save_cache()

        # Adım 1'i tekrar çalıştır (Fikstür ve Sakatlıkları tekrar gönder)
        # --- DEĞİŞİKLİK BURADA: 'df_injury_report' (Sakatlık) parametresi eklendi ---
        (report_lines, 
            top_players_final, 
            _, 
            current_season_players_df, 
            csv_inactive_player_names) = analysis_engine.get_players_for_hybrid_analysis(
                 df_games_today=df_games_today, # <--- Fikstür
                 df_oyuncu_mac=df_oyuncu_mac, 
                 df_oyuncu_sezon=df_oyuncu_sezon, 
                 nba_team_id_to_abbr=nba_team_id_to_abbr,
                 df_injury_report=df_injury_report # <--- YENİ
            )
        # --- DEĞİŞİKLİK BİTTİ ---
         
        if top_players_final is None:
            return render_template("index.html", 
                                   sonuclar="\n".join(report_lines), 
                                   top_2_picks=[], 
                                   all_results_ready=False)
        try:
            report_string, top_2_picks, all_adaylar = analysis_engine.run_full_analysis_logic(
                baremler=baremler,
                top_players_final=top_players_final,
                current_season_players_df=current_season_players_df,
                csv_inactive_player_names=csv_inactive_player_names, # <--- Bu değişken Adım 1'den geliyor
                df_oyuncu_mac=df_oyuncu_mac,
                df_takim_mac=df_takim_mac, 
                ANALYSIS_RANGE=ANALYSIS_RANGE,
                MINIMUM_PATTERN_PROBABILITY=MINIMUM_PATTERN_PROBABILITY,
                today_str=today_str 
            )
            
            all_adaylar_clean = clean_data_for_json(all_adaylar)
            top_2_picks_clean = clean_data_for_json(top_2_picks)

            session['last_full_analysis_results'] = all_adaylar_clean 
            session['last_diverse_recommendations'] = top_2_picks_clean
            
            analysis_log[today_str] = all_adaylar_clean
            save_log()
            
            return render_template("index.html", 
                                   sonuclar=report_string,
                                   top_2_picks=top_2_picks_clean, 
                                   all_results_ready=True) 

        except Exception as e:
            error_report = f"KRİTİK HATA:\n{e}\n\n{traceback.format_exc()}"
            return render_template("index.html", 
                                   sonuclar=error_report, 
                                   top_2_picks=[], 
                                   all_results_ready=False)


# ======================================================
# === BACKTEST TETİKLEYİCİLERİ ===
# ======================================================

@app.route('/run-backtest', methods=['POST'])
@login_required
def handle_backtest():
    with DATA_LOCK:
        date_str_key = request.form.get('log_date')
        if not date_str_key:
            return redirect(url_for('route_backtest'))
        if df_oyuncu_mac is None:
            print("HATA (Backtest): df_oyuncu_mac bellekte bulunamadı.")
            return redirect(url_for('route_backtest'))
        
        df_mac_results = df_oyuncu_mac.copy()
        saved_predictions = analysis_log.get(date_str_key, [])
        if not saved_predictions:
            return redirect(url_for('route_backtest'))
            
        (report_top4, 
         report_other, 
         report_summary, 
         _) = analysis_engine.run_backtest_logic(
             saved_predictions, df_mac_results, MINIMUM_PATTERN_PROBABILITY
         )
         
        sorted_dates = sorted(analysis_log.keys(), reverse=True)
        return render_template("backtest.html", 
                               log_dates=sorted_dates, 
                               selected_date=date_str_key,
                               report_top4=report_top4,
                               report_other=report_other,
                               report_summary=report_summary)

@app.route('/run-total-backtest')
@login_required
def handle_total_backtest():
    with DATA_LOCK:
        if df_oyuncu_mac is None:
            print("HATA (Total Backtest): df_oyuncu_mac bellekte bulunamadı.")
            return redirect(url_for('route_backtest'))
        df_mac_results = df_oyuncu_mac.copy()
        if not analysis_log:
            return redirect(url_for('route_backtest'))

        total_top_4_s = 0
        total_top_4_p = 0
        total_overall_s = 0
        total_overall_p = 0
        
        for date_str, predictions in analysis_log.items():
            if not predictions:
                continue
            (_, _, _, 
             (day_t4_s, day_t4_p, day_all_s, day_all_p)
             ) = analysis_engine.run_backtest_logic(
                 predictions, df_mac_results, MINIMUM_PATTERN_PROBABILITY
             )
            total_top_4_s += day_t4_s
            total_top_4_p += day_t4_p
            total_overall_s += day_all_s
            total_overall_p += day_all_p
        
        report_summary = []
        if total_top_4_p == 0:
            report_summary.append( (f"Top 4 Öneri Başarısı: %0.0 (0/0)", 'buyuk_kirmizi') )
        else:
            top_4_rate = (total_top_4_s / total_top_4_p) * 100
            report_summary.append( (f"Top 4 Öneri Başarısı: %{top_4_rate:.1f} ({total_top_4_s}/{total_top_4_p})", 'buyuk_yesil') )
            
        if total_overall_p == 0:
            report_summary.append( (f"Tüm Analizler Başarısı: %0.0 (0/0)", 'buyuk_kirmizi') )
        else:
            total_rate = (total_overall_s / total_overall_p) * 100
            report_summary.append( (f"Tüm Analizler Başarısı: %{total_rate:.1f} ({total_overall_s}/{total_overall_p})", 'buyuk_yesil') )
        
        sorted_dates = sorted(analysis_log.keys(), reverse=True)
        return render_template("backtest.html", 
                               log_dates=sorted_dates, 
                               selected_date=f"TOTAL ({len(analysis_log)} GÜN)",
                               report_top4=[], 
                               report_other=[],
                               report_summary=report_summary)

# --- YENİ LOG SİLME FONKSİYONLARI BURAYA EKLENDİ ---
@app.route('/clear-logs', methods=['POST'])
@login_required
def handle_clear_logs():
    """
    'Tüm Logları Temizle' butonuna basıldığında çalışır.
    'analysis_log.json' dosyasının içini boşaltır.
    """
    global analysis_log
    
    print("Tüm analiz loglarını silme talebi alındı...")
    
    with DATA_LOCK:
        try:
            analysis_log = {}
            save_log() 
            print("Başarılı: 'analysis_log.json' dosyası temizlendi.")
        except Exception as e:
            print(f"HATA: Loglar temizlenirken hata oluştu: {e}")
    
    return redirect(url_for('route_backtest'))

@app.route('/delete-log-date', methods=['POST'])
@login_required
def handle_delete_log_date():
    """
    Kullanıcı 'Seçilen Tarihi Sil' butonuna bastığında çalışır.
    Formdan 'log_date' anahtarını alır ve analysis_log'dan siler.
    """
    global analysis_log
    
    date_to_delete = request.form.get('log_date')
    
    if not date_to_delete:
        print("HATA: Silinecek tarih seçilmedi.")
        return redirect(url_for('route_backtest'))

    print(f"'{date_to_delete}' tarihli analiz logunu silme talebi alındı...")
    
    with DATA_LOCK:
        try:
            if date_to_delete in analysis_log:
                del analysis_log[date_to_delete]
                save_log() 
                print(f"Başarılı: '{date_to_delete}' tarihi loglardan silindi.")
            else:
                print(f"UYARI: '{date_to_delete}' tarihi logda bulunamadı (zaten silinmiş olabilir).")
        except Exception as e:
            print(f"HATA: Log '{date_to_delete}' tarihi silinirken hata oluştu: {e}")
    
    return redirect(url_for('route_backtest'))
# --- YENİ FONKSİYONLARIN EKLENMESİ BİTTİ ---


# ======================================================
# === UYGULAMAYI BAŞLAT ===
# ======================================================

try: 
    load_data_from_s3() 
except Exception as e:
    print("="*50)
    print(f"KRİTİK BAŞLANGIÇ HATASI: {e}")
    print("Veritabanı S3'ten indirilemedi veya okunamadı.")
    print("Uygulama, '/veri-guncelle' sayfası hariç düzgün çalışmayacak.")
    print("="*50)

load_cache()
load_log()


if __name__ == "__main__":
    print("Flask sunucusu LOKALDE (debug modda) http://0.0.0.0:5002 adresinde başlatılıyor...")
    port = int(os.environ.get('PORT', 5002))
    app.run(debug=True, host='0.0.0.0', port=port, use_reloader=True)