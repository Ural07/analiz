# 'jsonify' (JSON yanıtları için), 'threading' (arka plan), 'subprocess' (script çalıştırma) ve 'sys' (python yolu) eklendi
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify 
import pandas as pd
import numpy as np # (Tip kontrolü)
from datetime import datetime, timedelta
import traceback # Hata ayıklama için
import json
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from functools import wraps
import time

# --- YENİ KÜTÜPHANELER ---
import subprocess # Harici script (veri_cek.py) çalıştırmak için
import sys # Python'un yolunu (sys.executable) bulmak için
import threading # İşlemleri (veri_cek.py) arka plana atmak için
# --- BİTTİ ---

# b40.py'den dönüştürdüğümüz "beyin" dosyamızı içe aktar
import analysis_engine 

# --- DEĞİŞİKLİK 1: MUTLAK DİZİNİ TANIMLA ---
# app.py'nin bulunduğu klasörün tam yolunu al
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"Uygulama Ana Dizini (BASE_DIR): {BASE_DIR}")
# --- BİTTİ ---

# ======================================================
# === UYGULAMA YAPILANDIRMASI (CONFIG) ===
# ======================================================
# --- YENİ VERİTABANI BAĞLANTISI (NEON) ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL is None:
    print("KRİTİK HATA: .env dosyasında DATABASE_URL bulunamadı.")
    exit()
try:
    pool_pre_ping = True
    pool_recycle = 300
    engine = create_engine(DATABASE_URL)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    print("Bulut (Neon) Veritabanı bağlantısı başarılı (app.py).")
except Exception as e:
    print(f"KRİTİK HATA: Bulut veritabanına bağlanılamadı: {e}")
    exit()
# --- Analiz Ayarları ---
ANALYSIS_RANGE = 4.0 # 4 aşağı, 4 yukarı
MINIMUM_PATTERN_PROBABILITY = 75.0 # Desen olasılığı barajı
TOP_N_PLAYERS_PER_TEAM = 5 # Her takımdan kaç oyuncu listelenecek
CURRENT_SEASON_START_DATE = '2025-09-01' # Sezon başı filtre tarihi

# --- Dosya Yolları ---

CACHE_FILE = "barem_cache.json"
LOG_FILE = "analysis_log.json"

# --- Global Değişkenler ---
# (Sunucu başlarken 'load_dataframes' ile doldurulacak)
df_oyuncu_mac = None
df_oyuncu_sezon = None
df_takim_mac = None
ALL_TEAMS_LIST = []
ALL_PLAYERS_LIST = []
# (nba_api'den alınacak)
nba_team_id_to_abbr = {}
nba_abbr_to_id = {}
# (Hafıza / Cache)
cached_barems = {}
cached_player_list_key = ""
analysis_log = {}

# --- YENİ GLOBAL DEĞİŞKENLER (Veri Güncelleme için) ---
# 'veri_cek.py' çalışırken başka bir analiz yapılmasını engellemek için 'Kilit'
DATA_LOCK = threading.Lock() 

# 'veri_cek.py' scriptinin durumunu arayüzde göstermek için
update_status = { 
    "is_running": False,
    "message": "Henüz bir güncelleme başlatılmadı.",
    "last_run_time": None
}
# --- BİTTİ ---


# ======================================================
# === VERİ YÜKLEME (b40.py'nin __main__ bloğundan alındı) ===
# ======================================================

def load_dataframes():
    """
    Tüm tabloları sunucu başlarken (veya güncelleme sonrası) 
    BULUT VERİTABANINDAN (Neon) okuyup belleğe yükler.
    Bu fonksiyon artık 'DATA_LOCK' ile korunuyor.
    """
    global df_oyuncu_mac, df_oyuncu_sezon, df_takim_mac, ALL_TEAMS_LIST, ALL_PLAYERS_LIST
    global nba_team_id_to_abbr, nba_abbr_to_id
    
    print("Veri kilidi alınıyor (load_dataframes)...") 
    with DATA_LOCK: 
        try:
            print("Bulut (Neon) veritabanından tablolar okunuyor...")
            
            # --- DEĞİŞİKLİK: pd.read_csv yerine pd.read_sql_query ---
            # 'engine' değişkeni, Adım 2'de tanımladığımız global motordur.
            # Tablo isimleri, veri_cek.py'nin yazdığı isimlerle aynı olmalı.
            
            df_oyuncu_mac = pd.read_sql_query("SELECT * FROM oyuncu_mac_performanslari", con=engine)
            df_oyuncu_sezon = pd.read_sql_query("SELECT * FROM oyuncu_sezon_istatistikleri", con=engine)
            df_takim_mac = pd.read_sql_query("SELECT * FROM maclar", con=engine)
            
            print("Veritabanından okuma tamamlandı. Veri tipleri dönüştürülüyor...")
            
            # --- Veri Tipi Dönüşümleri (HİÇ DEĞİŞMEDİ, AYNI KALIYOR) ---
            cols_mac = ['PTS', 'FGA', 'FGM', 'FG_PCT', 'FTA', 'FTM', 'FT_PCT', 'REB', 'AST']
            for col in cols_mac:
                if col in df_oyuncu_mac.columns:
                    # 'Int64' (büyük harf I) sütunları (örn: PLAYER_ID) zaten doğru gelmeli,
                    # ama sayısal (float) sütunları to_numeric ile garantiye alıyoruz.
                    df_oyuncu_mac[col] = pd.to_numeric(df_oyuncu_mac[col], errors='coerce').fillna(0)
            
            cols_sezon = ['GP', 'MIN', 'PTS', 'FGA', 'FGM', 'FTA', 'FTM', 'REB', 'AST']
            for col in cols_sezon:
                 if col in df_oyuncu_sezon.columns:
                    df_oyuncu_sezon[col] = pd.to_numeric(df_oyuncu_sezon[col], errors='coerce').fillna(0)
            
            df_takim_mac['PTS'] = pd.to_numeric(df_takim_mac['PTS'], errors='coerce').fillna(0)

            # --- Tarih Dönüşümleri (HİÇ DEĞİŞMEDİ, AYNI KALIYOR) ---
            df_oyuncu_mac['GAME_DATE'] = pd.to_datetime(df_oyuncu_mac['GAME_DATE'], errors='coerce')
            df_takim_mac['GAME_DATE'] = pd.to_datetime(df_takim_mac['GAME_DATE'], errors='coerce')
            
            df_oyuncu_mac = df_oyuncu_mac.dropna(subset=['GAME_DATE', 'PLAYER_ID', 'GAME_ID'])
            df_takim_mac = df_takim_mac.dropna(subset=['GAME_DATE']) 

            # --- Listeleri Oluştur (Dropdown'lar için) ---
            ALL_PLAYERS_LIST = sorted(df_oyuncu_mac['PLAYER_NAME'].unique())
            ALL_TEAMS_LIST = sorted(df_takim_mac['TEAM_NAME'].unique())
            
            print(f"Başarılı: {len(ALL_PLAYERS_LIST)} oyuncu, {len(ALL_TEAMS_LIST)} takım belleğe yüklendi.")
            
        except Exception as e:
            # Artık FileNotFoundError değil, genel bir veritabanı hatası olabilir
            print(f"KRİTİK HATA: Bulut veritabanından veri yüklenirken hata oluştu: {e}")
            print(traceback.format_exc())
            raise e 
        
        # NBA API Haritasını Yükle (HİÇ DEĞİŞMEDİ, AYNI KALIYOR)
        try:
            from nba_api.stats.static import teams as nba_static_teams
            nba_teams_all = nba_static_teams.get_teams()
            nba_team_id_to_abbr = {team['id']: team['abbreviation'] for team in nba_teams_all}
            nba_abbr_to_id = {team['abbreviation']: team['id'] for team in nba_teams_all}
        except Exception:
             print("UYARI: nba-api 'get_teams' çağrısı başarısız. Bellekten yedek harita oluşturuluyor.")
             # Artık CSV değil, bellekteki df_oyuncu_sezon'u kullanıyoruz
             team_data = df_oyuncu_sezon[['TEAM_ID', 'TEAM_ABBREVIATION']].drop_duplicates()
             nba_team_id_to_abbr = pd.Series(
                 team_data['TEAM_ABBREVIATION'].values, 
                 index=team_data['TEAM_ID']
             ).to_dict()
             nba_abbr_to_id = pd.Series(
                 team_data['TEAM_ID'].values, 
                 index=team_data['TEAM_ABBREVIATION']
             ).to_dict()
    print("Veri kilidi serbest bırakıldı (load_dataframes).")
# ======================================================
# === VERİ GÜNCELLEME MOTORU (Arka Plan) ===
# ======================================================

# ======================================================
# === VERİ GÜNCELLEME MOTORU (Arka Plan) ===
# ======================================================

# ======================================================
# === VERİ GÜNCELLEME MOTORU (Arka Plan) ===
# ======================================================

def run_data_update_in_background(): 
    """
    veri_cek.py scriptini bir alt process'te (subprocess) çalıştırır.
    Bu fonksiyon bir 'threading.Thread' içinde çalıştırılmak içindir.
    Arayüzü (Flask) KİLİTLEMEZ.
    """
    global update_status
    
    # 1. Durumu "Çalışıyor" olarak ayarla
    update_status["is_running"] = True
    update_status["message"] = f"[{datetime.now().strftime('%H:%M:%S')}] Veri güncelleme işlemi başladı (veri_cek.py bulut veritabanını güncelliyor)..."
    
    try:
        # app.py'nin bulunduğu klasörün yolunu al
        base_dir = os.path.dirname(os.path.abspath(__file__))
        # `veri_cek.py`'nin tam yolunu oluştur
        script_path = os.path.join(base_dir, "veri_cek.py")
        
        if not os.path.exists(script_path):
             raise FileNotFoundError(f"Script bulunamadı: {script_path}")

        print(f"Arka planda çalıştırılıyor: {sys.executable} {script_path}")

        # [ "python", "veri_cek.py" ] komutunu çalıştır
        result = subprocess.run(
            [sys.executable, script_path], 
            cwd=base_dir,         # Çalışma dizinini zorla (bu doğru)
            capture_output=True,  
            text=True,            
            check=True,           
            encoding='utf-8'
        )
        
        # 2. `veri_cek.py` Başarılı Olduysa
        print("veri_cek.py başarıyla tamamlandı. Çıktı:", result.stdout)
        
        update_status["message"] = f"[{datetime.now().strftime('%H:%M:%S')}] Veri çekme tamamlandı. 2 saniye bekleniyor (Veritabanı senkronizasyonu için)..."
        time.sleep(2.0) # Veritabanının yazmayı bitirmesi için kısa bir bekleme
        
        update_status["message"] = f"[{datetime.now().strftime('%H:%M:%S')}] BAŞARILI: Yeni veriler buluttan okunup belleğe yükleniyor..."
        
        # 3. YENİ VERİLERİ YÜKLE
        # Bu fonksiyon artık CSV'den değil, BULUTTAN okuyacak
        load_dataframes() 
        
        update_status["message"] = f"[{datetime.now().strftime('%H:%M:%S')}] BAŞARILI: Tüm veriler güncellendi ve bellek yenilendi."
        
    except subprocess.CalledProcessError as e:
        # Script hata verirse
        error_output = e.stderr or e.stdout 
        print(f"HATA (veri_cek.py): {error_output}")
        update_status["message"] = f"[{datetime.now().strftime('%H:%M:%S')}] HATA: veri_cek.py başarısız oldu. Hata: {error_output}"
        
    except FileNotFoundError as e:
        # `veri_cek.py` dosyası bulunamazsa
        print(f"HATA (Script Yolu): {e}")
        update_status["message"] = f"[{datetime.now().strftime('%H:%M:%S')}] HATA (FileNotFound): Script yolu yanlış. {e}"

    except Exception as e:
        # Başka beklenmedik bir hata olursa
        print(f"KRİTİK HATA (run_data_update): {e}")
        update_status["message"] = f"[{datetime.now().strftime('%H:%M:%S')}] KRİTİK HATA: Güncelleyici çalıştırılamadı. Hata: {str(e)}"
        
    finally:
        # Ne olursa olsun 'çalışıyor' durumunu kapat
        update_status["is_running"] = False
        update_status["last_run_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print("Arka plan işlemi tamamlandı.")

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


# --- ESKİ clean_data_for_json FONKSİYONUNU SİLİN ---

# --- YERİNE BU GÜÇLENDİRİLMİŞ FONKSİYONU YAPIŞTIRIN ---
def clean_data_for_json(data_list): 
    """
    Numpy/Pandas tiplerini (int64, float64, nan, NaT) içeren bir sözlük listesini
    JSON'a (ve Flask session'a) uygun standart Python tiplerine (int, float, None) dönüştürür.
    """
    cleaned_list = []
    if not isinstance(data_list, list):
        # Veri bozuksa veya liste değilse, boş liste döndür
        return []

    for item_dict in data_list:
        if not isinstance(item_dict, dict):
            cleaned_list.append(item_dict)
            continue
            
        cleaned_item = {}
        for key, value in item_dict.items():
            
            # 1. Önce NaN/NaT gibi "null" değerleri yakala
            # pd.isna() hem np.nan, hem pd.NaT, hem de None'ı yakalar
            if pd.isna(value):
                cleaned_item[key] = None
            
            # 2. Numpy'ye özel tamsayıları (int64 vb.) Python int'e çevir
            elif isinstance(value, np.integer): 
                cleaned_item[key] = int(value)
            
            # 3. Numpy'ye özel float'ları (float64 vb.) Python float'a çevir
            elif isinstance(value, np.floating): 
                # np.nan zaten üstteki pd.isna() ile yakalandı,
                # bu yüzden burası güvenli
                cleaned_item[key] = float(value)
            
            # 4. Numpy'ye özel bool'ları Python bool'a çevir
            elif isinstance(value, np.bool_): 
                cleaned_item[key] = bool(value)
            
            # 5. Geri kalan her şey (string, normal int, normal float)
            else:
                cleaned_item[key] = value 
        cleaned_list.append(cleaned_item)
    return cleaned_list


# --- Flask Uygulaması ---
app = Flask(__name__)
# Gizli anahtar, kullanıcı oturumları (login) için gereklidir
app.secret_key = 'sizin-cok-gizli-anahtariniz-12345' 

# ======================================================
# === KULLANICI GİRİŞ (LOGIN) SİSTEMİ (Talep 1) ===
# ======================================================
# Basit bir kullanıcı adı ve şifre (Render'da Ortam Değişkeni olarak ayarlanmalı)
ADMIN_USERNAME = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASS', '12345')

def login_required(f):
    """Kullanıcının giriş yapıp yapmadığını kontrol eden wrapper"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('route_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function 

def api_login_required(f):
    """
    API endpoint'leri için 'login_required'.
    Redirect (yönlendirme) yapmak yerine 401 (Unauthorized) JSON hatası döndürür.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            # Yönlendirme (redirect) yapma!
            # Bunun yerine JSON hatası ve 401 (Yetkisiz) kodu döndür.
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
            # 'next' parametresi varsa oraya yönlendir, yoksa ana sayfaya
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
    """ Ana sayfa (Günün Analizi sekmesi) """
    return render_template("index.html", 
                           sonuclar=None, 
                           top_2_picks=[], 
                           all_results_ready=False)

@app.route('/oyuncu')
@login_required
def route_oyuncu():
    """ Oyuncu Analizi sekmesi (GET) """
    return render_template("oyuncu.html", players=ALL_PLAYERS_LIST, report_string=None)

@app.route('/takim')
@login_required
def route_takim():
    """ Takım Analizi sekmesi (GET) """
    return render_template("takim.html", teams=ALL_TEAMS_LIST, report_string=None)

@app.route('/backtest')
@login_required
def route_backtest():
    """ Analiz Başarısı sekmesi (GET) """
    sorted_dates = sorted(analysis_log.keys(), reverse=True)
    return render_template("backtest.html", log_dates=sorted_dates)

@app.route('/veri-guncelle') 
@login_required
def route_veri_guncelle():
    """Veri güncelleme sayfasını (yeni sekme) gösterir."""
    return render_template("veri_guncelle.html")


# --- BU FONKSİYON TAMAMEN DEĞİŞTİ ---
# ... (app.py'nin üst kısımları, route_veri_guncelle fonksiyonu) ...

# <--- GÜNCELLEME BAŞLANGICI (route_browse_data) ---
@app.route('/browse-data') 
@app.route('/browse-data/<string:file>') # <--- BUTONLARDAN GELEN İSTEK İÇİN BUNU DA EKLEDİM
@login_required
def route_browse_data(file=None): # <--- 'file' parametresini al
    """
    Bellekteki (Global DataFrame) CSV verilerini bir tabloda gösterir.
    Sadece sayfayı (kabuğu) yükler, veri AJAX ile çekilir.
    """
    
    # Hangi dosyayı görmek istediğimizi URL'den al
    # Hem /browse-data?file=... hem de /browse-data/oyuncu_mac destekler
    file_key = request.args.get('file') or file
    
    column_names = []
    file_name = ""
    data_shape = (0, 0)
    
    # Veri güncelleme işlemi (veri_cek.py) çalışırken
    # bellekteki verileri okumak güvenli değildir. Kilidi bekleyelim.
    print("Veri kilidi alınıyor (Veri Gözat - Kabuk)...")
    with DATA_LOCK:
        print("Veri kilidi alındı (Veri Gözat - Kabuk).")
        target_df = None # Hedef DataFrame
        
        if file_key == 'oyuncu_mac' and df_oyuncu_mac is not None:
            file_name = "oyuncu_mac_performanslari.csv"
            target_df = df_oyuncu_mac
            
        elif file_key == 'oyuncu_sezon' and df_oyuncu_sezon is not None:
            file_name = "oyuncu_sezon_istatistikleri.csv"
            target_df = df_oyuncu_sezon
            
        elif file_key == 'takim_mac' and df_takim_mac is not None:
            file_name = "maclar.csv"
            target_df = df_takim_mac
            
        if target_df is not None:
            # Sadece meta-veriyi al (shape ve columns)
            data_shape = target_df.shape
            column_names = list(target_df.columns)
            
            # <--- SİLİNDİ: Dev JSON verisi burada ARTIK OLUŞTURULMUYOR ---
            # data_json = temp_df.to_json(orient='records')
            # <--- SİLME BİTTİ ---
        
    print("Veri kilidi serbest bırakıldı (Veri Gözat - Kabuk).")
            
    # Sütun listesini de JSON string olarak gönder
    column_names_json = json.dumps(column_names)
    # Dil URL'sini de buradan gönder
    datatable_lang_url = "https://cdn.datatables.net/plug-ins/2.0.8/i18n/tr.json"
    
    # browse_data.html şablonuna verileri gönder
    return render_template(
        "browse_data.html",
        file_name=file_name,
        data_shape=data_shape,
        column_names=column_names, # Bu 'thead' (başlık) için hala gerekli
        
        # <--- DEĞİŞTİ: 'data_json' GÖNDERİLMİYOR ---
        
        column_names_json=column_names_json, # <--- Düzeltildi (JS için)
        current_file_key=file_key,           # <--- Düzeltildi (JS'nin hangi API'yi çağıracağını bilmesi için)
        datatable_lang_url=datatable_lang_url 
    )
# <--- GÜNCELLEME BİTTİ (route_browse_data) ---


# <--- GÜNCELLEME BAŞLANGICI (route_get_data) ---
# Bu fonksiyon, DataTables'ın AJAX ile veri çekeceği yerdir.
@app.route('/api/get_data/<string:file_key>')
@api_login_required 
def route_get_data(file_key):
    """
    DataTables'ın AJAX ile veri çekmesi için API endpoint'i.
    Sadece JSON döndürür.
    """
    print(f"API verisi talep edildi: {file_key}")

    print("Veri kilidi alınıyor (API)...")
    with DATA_LOCK:
        print("Veri kilidi alındı (API).")
        target_df = None
        
        # Doğru global DataFrame'i seç
        if file_key == 'oyuncu_mac' and df_oyuncu_mac is not None:
            target_df = df_oyuncu_mac
        elif file_key == 'oyuncu_sezon' and df_oyuncu_sezon is not None:
            target_df = df_oyuncu_sezon
        elif file_key == 'takim_mac' and df_takim_mac is not None:
            target_df = df_takim_mac
        else:
            print("Veri kilidi serbest bırakıldı (API - Hata).")
            return jsonify({"error": "Geçersiz dosya anahtarı veya veri yüklenmemiş", "data": []}), 404

        try:
            temp_df = target_df.copy()
            if 'GAME_DATE' in temp_df.columns:
                temp_df['GAME_DATE'] = temp_df['GAME_DATE'].dt.date.astype(str).replace('NaT', None)
            data_records = temp_df.to_dict('records')

            # --- ŞU SATIRI EKLE: Her veri tipinde limiti uygula ---
            #data_limit = 9
            #data_records = data_records[:data_limit]
            # ------------------------------------------------------

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
    """ 'Tüm Sonuçları Göster' butonu için yeni tam sayfa rota """
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
                           MINIMUM_PATTERN_PROBABILITY=MINIMUM_PATTERN_PROBABILITY # Bu satır düzeltilmişti
                           )


# ======================================================
# === YÖNETİM PANELİ API ROTALARI (Değişmedi) ===
# ======================================================

@app.route('/start-update', methods=['POST']) 
@login_required
def handle_start_update():
    """
    Kullanıcı 'Güncellemeyi Başlat' butonuna bastığında (JavaScript ile) bu çağrılır.
    """
    global update_status
    
    if update_status["is_running"]:
        return jsonify({
            "success": False, 
            "message": "Hata: Güncelleme zaten çalışıyor. Lütfen bitmesini bekleyin."
        }), 409 
    
    update_thread = threading.Thread(target=run_data_update_in_background, daemon=True) 
    update_thread.start() 
    
    return jsonify({
        "success": True,
        "message": "Güncelleme işlemi arka planda başarıyla başlatıldı. Durum panelini takip edin."
    })

@app.route('/get-update-status', methods=['GET']) 
@login_required
def handle_get_status():
    """
    Kullanıcı arayüzü (JavaScript) 'Durum nedir?' diye sormak için bunu çağırır.
    """
    global update_status
    return jsonify(update_status)

# ======================================================
# === ANALİZ TETİKLEYİCİLERİ (Butonların Tıklanacağı Yer) ===
# ======================================================

@app.route('/takim-analizi', methods=['POST'])
@login_required
def handle_team_analysis():
    """
    Kullanıcı 'Takım Analizi' sekmesindeki butona tıkladığında bu fonksiyon çalışır.
    """
    with DATA_LOCK: # (Veri yüklenirken analiz yapılmasın)
        try:
            team_name = request.form.get('team_name')
            threshold_str = request.form.get('threshold', '105.5')
            threshold = float(threshold_str)
            
            print(f"Takım analizi talebi alındı: {team_name} @ {threshold}")

            report_string = analysis_engine.analyze_team_logic(
                team_name=team_name,
                threshold=threshold,
                df_takim_mac=df_takim_mac
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
    """
    Kullanıcı 'Oyuncu Analizi' sekmesindeki butona tıkladığında bu fonksiyon çalışır.
    """
    with DATA_LOCK: # (Veri yüklenirken analiz yapılmasın)
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

# app.py DOSYANIZDAKİ ESKİ handle_get_players'I SİLİP, BUNU YAPIŞTIRIN:

# app.py

@app.route('/get-players')
@login_required
def handle_get_players():
    """
    'OYUNCU LİSTESİ AL' butonu tıklandığında çalışır.
    (Artık 'basketball-reference.com' kazıyıcısını kullanır)
    """
    global cached_barems, cached_player_list_key, nba_team_id_to_abbr, nba_abbr_to_id
    
    # --- DÜZELTME BURADA ---
    # Değişkenlere, 'try' bloğuna girmeden önce varsayılan değerler atayalım.
    # Bu, 'today_str tanımlanmadı' hatasını çözecektir.
    today_str = datetime.now().strftime('%Y-%m-%d')
    report_lines = []
    top_players_final = None
    grouped_players = {}
    # --- DÜZELTME BİTTİ ---
    
    with DATA_LOCK: # (Veri yüklenirken analiz yapılmasın)
        print("Oyuncu listesi alma talebi alındı...")
        
        try:
            # Bu fonksiyon artık 'analysis_engine' içindeki kazıyıcıyı çağırıyor
            (report_lines, 
             top_players_final, 
             today_str, # Değişken burada güncellenecek
             current_season_players_df, 
             csv_inactive_player_names) = analysis_engine.get_players_for_hybrid_analysis(
                 df_oyuncu_mac,
                 df_oyuncu_sezon,
                 nba_team_id_to_abbr,
                 nba_abbr_to_id # Bu yeni parametreyi eklemiştik
             )
        except Exception as e:
            # 'try' bloğu başarısız olursa
            error_report = f"KRİTİK HATA (analysis_engine): {e}\n\n{traceback.format_exc()}"
            print(error_report)
            return render_template("index.html", 
                                   sonuclar=error_report, 
                                   top_2_picks=[], 
                                   all_results_ready=False)

        # 2. Eğer API veya filtreleme başarısız olursa (örn: maç yoksa)
        if top_players_final is None:
            sonuclar = "\n".join(report_lines)
            return render_template("index.html", 
                                   sonuclar=sonuclar, 
                                   top_2_picks=[], 
                                   all_results_ready=False)

        # 3. Hafıza (Cache) Kontrolü
        player_names_for_popup = sorted(top_players_final['PLAYER_NAME'].tolist())
        current_player_list_key = "-".join(player_names_for_popup)
        
        if current_player_list_key != cached_player_list_key:
            report_lines.append("Yeni oyuncu listesi algılandı. Barem hafázası sıfırlanıyor...")
            cached_barems = {} 
            cached_player_list_key = current_player_list_key 
        else:
            report_lines.append("Hafızadaki baremler (cache) kullanılacak.")
        
        # 4. GRUPLAMA MANTIĞI 
        # (Gruplama işlemini 'top_players_final' None değilse yap)
        if top_players_final is not None:
            # 'sort=False' ile API'den gelen maç sırasını (GAME_ID) koru
            for matchup_name, group_df in top_players_final.groupby('MATCHUP', sort=False):
                # O maça ait oyuncuları (DataFrame grubunu) listeye çevir
                grouped_players[matchup_name] = group_df.to_dict('records')
        
        # 5. Gruplanmış veriyi 'index.html'e geri gönder.
        # Bu 'return' satırı artık 'today_str'nin mutlaka tanımlı olduğunu biliyor.
        return render_template("index.html", 
                               sonuclar="\n".join(report_lines),
                               players_to_analyze_grouped=grouped_players, # Gruplanmış veri
                               cached_barems=cached_barems, 
                               today_str=today_str,
                               all_results_ready=False)

@app.route('/run-analysis', methods=['POST'])
@login_required
def handle_run_analysis():
    """
    Kullanıcı 'Barem Gir' ekranından 'Analizi Başlat'a tıkladığında çalışır.
    """
    global cached_barems, analysis_log
    
    with DATA_LOCK: # (Veri yüklenirken analiz yapılmasın)
        print("Tam analiz talebi alındı...")
        baremler = [] 
        
        # 1. Formdan gelen tüm baremleri topla
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
                
        # 2. Barem hafızasını güncelle ve kaydet
        cached_barems.update(barem_dict)
        save_cache()

        # 3. Analizi yapmak için Adım 1'deki oyuncu verilerini YENİDEN OLUŞTUR
        (report_lines, 
            top_players_final, 
            _, 
            current_season_players_df, 
            csv_inactive_player_names) = analysis_engine.get_players_for_hybrid_analysis(
                df_oyuncu_mac, 
                df_oyuncu_sezon, 
                nba_team_id_to_abbr,
                nba_abbr_to_id  # <-- YENİ EKLENEN PARAMETRE
 )
         
        if top_players_final is None:
            return render_template("index.html", 
                                   sonuclar="\n".join(report_lines), 
                                   top_2_picks=[], 
                                   all_results_ready=False)

        # 4. 'analysis_engine.py' içindeki YENİ tam analiz mantığını çağır
        try:
            report_string, top_2_picks, all_adaylar = analysis_engine.run_full_analysis_logic(
                baremler=baremler,
                top_players_final=top_players_final,
                current_season_players_df=current_season_players_df,
                csv_inactive_player_names=csv_inactive_player_names,
                df_oyuncu_mac=df_oyuncu_mac,
                df_takim_mac=df_takim_mac,
                ANALYSIS_RANGE=ANALYSIS_RANGE,
                MINIMUM_PATTERN_PROBABILITY=MINIMUM_PATTERN_PROBABILITY,
                today_str=today_str 
            )
            
            # --- HATA DÜZELTMESİ (Aynen kaldı) ---
            # 5. Sonuçları kaydet (JSON'a uygun tiplere dönüştürdükten sonra)
            all_adaylar_clean = clean_data_for_json(all_adaylar)
            top_2_picks_clean = clean_data_for_json(top_2_picks)

            session['last_full_analysis_results'] = all_adaylar_clean 
            session['last_diverse_recommendations'] = top_2_picks_clean
            
            analysis_log[today_str] = all_adaylar_clean
            save_log()
            
            # 6. Sonuçları ana sayfaya gönder
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
    """
    'Analiz Başarısı' sekmesindeki 'SEÇİLEN TARİHİ KONTROL ET' butonunu çalıştırır.
    """
    with DATA_LOCK: # (Log okunurken/yazılırken veya data yüklenirken kilit)
        date_str_key = request.form.get('log_date')
        if not date_str_key:
            return redirect(url_for('route_backtest'))
            
        # --- DEĞİŞİKLİK: CSV'den okumak yerine, bellekteki (RAM) DataFrame'i kopyala ---
        if df_oyuncu_mac is None:
            print("HATA (Backtest): df_oyuncu_mac bellekte bulunamadı.")
            return redirect(url_for('route_backtest'))
        
        df_mac_results = df_oyuncu_mac.copy()
        # --- DEĞİŞİKLİK BİTTİ ---
            
        # 2. Logdan o günün verisini al
        saved_predictions = analysis_log.get(date_str_key, [])
        if not saved_predictions:
            return redirect(url_for('route_backtest'))
            
        # 3. Analiz motorunu çağır (Bu fonksiyon değişmedi)
        (report_top4, 
         report_other, 
         report_summary, 
         _) = analysis_engine.run_backtest_logic(
             saved_predictions, df_mac_results, MINIMUM_PATTERN_PROBABILITY
         )
         
        # 4. Sonuçları 'backtest.html'e gönder
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
    """
    'Analiz Başarısı' sekmesindeki 'TÜM LOGLARI KONTROL ET' butonunu çalıştırır.
    """
    with DATA_LOCK: # (Tüm loglar okunurken kilit)
        
        # --- DEĞİŞİKLİK: CSV'den okumak yerine, bellekteki (RAM) DataFrame'i kopyala ---
        if df_oyuncu_mac is None:
            print("HATA (Total Backtest): df_oyuncu_mac bellekte bulunamadı.")
            return redirect(url_for('route_backtest'))
            
        df_mac_results = df_oyuncu_mac.copy()
        # --- DEĞİŞİKLİK BİTTİ ---
            
        if not analysis_log:
            return redirect(url_for('route_backtest'))

        # Toplayıcıları başlat
        total_top_4_s = 0
        total_top_4_p = 0
        total_overall_s = 0
        total_overall_p = 0
        
        # Tüm loglar üzerinde döngü
        for date_str, predictions in analysis_log.items():
            if not predictions:
                continue
                
            # Her gün için başarıyı hesapla ve topla
            (_, _, _, 
             (day_t4_s, day_t4_p, day_all_s, day_all_p) # Bu 'return'u bir önceki adımda düzeltmiştik
             ) = analysis_engine.run_backtest_logic(
                 predictions, df_mac_results, MINIMUM_PATTERN_PROBABILITY
             )
             
            total_top_4_s += day_t4_s
            total_top_4_p += day_t4_p
            total_overall_s += day_all_s
            total_overall_p += day_all_p
        
        # Total Raporu Oluştur (Burası aynı)
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


# ======================================================
# === UYGULAMAYI BAŞLAT ===
# ======================================================
if __name__ == "__main__":
    # Sunucu başlamadan önce CSV'leri yükle
    try: 
        load_dataframes()
    except Exception as e:
        print("="*50)
        print(f"KRİTİK BAŞLANGIÇ HATASI: {e}")
        print("CSV dosyaları yüklenemedi. 'veri_cek.py' scriptini çalıştırmanız veya")
        print("CSV dosyalarının 'app.py' ile aynı dizinde olduğundan emin olmanız gerekebilir.")
        print("Uygulama, '/veri-guncelle' sayfası hariç düzgün çalışmayacak.")
        print("="*50)
    
    # Sunucu başlamadan önce Kalıcı Hafızayı yükle
    load_cache()
    load_log()
    
    print("Flask sunucusu http://0.0.0.0:5002 adresinde başlatılıyor...")
    app.run(debug=True, host='0.0.0.0', port=5002, use_reloader=True)