import time
import pandas as pd
import os
from dotenv import load_dotenv  # Gizli anahtarları (.env) okumak için
from sqlalchemy import create_engine, text # PostgreSQL (Neon) bağlantısı için

from nba_api.stats.static import teams, players
from nba_api.stats.endpoints import leaguegamefinder, leaguedashplayerstats, leaguegamelog

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Accept': 'application/json; charset=utf-8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nba.com/',
    'Connection': 'keep-alive',
    'Origin': 'https://www.nba.com'
}
# === BİTTİ ===

# --- AYARLAR ---
GECEN_SEZON = '2024-25'  
BU_SEZON = '2025-26'      

# ==================================================
# === YENİ VERİTABANI AYARLARI (NEON) ===
# ==================================================
# .env dosyasındaki gizli DATABASE_URL'yi yükle
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL is None:
    print("KRİTİK HATA: DATABASE_URL bulunamadı.")
    print("Lütfen projenizin ana dizininde .env dosyasının olduğundan")
    print("ve içinde DATABASE_URL='postgresql://...' adresinin yazdığından emin olun.")
    exit() # Hata varsa script'i durdur

# Yeni bulut veritabanı motorunu (engine) oluştur
# Bu 'engine' değişkeni, dosyanın geri kalanındaki tüm 'to_sql' komutları tarafından kullanılacak
try:
    engine = create_engine(DATABASE_URL)
    print("Bulut (Neon) veritabanına bağlanılıyor...")
    # Bağlantıyı test etmek için küçük bir sorgu
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    print("Veritabanı bağlantısı başarılı.")
except Exception as e:
    print(f"KRİTİK HATA: Bulut veritabanına bağlanılamadı: {e}")
    print("Lütfen .env dosyasındaki DATABASE_URL adresinizi tekrar kontrol edin.")
    exit()
# ==================================================


# --- FONKSİYON 1: Takımlar (Değişiklik yok) ---
def takimlari_getir():
    print("--- Takım verileri çekiliyor... ---")
    try:
        gelen_takimlar_listesi = teams.get_teams()
        df_takimlar = pd.DataFrame(gelen_takimlar_listesi)
        print(f"Toplam {len(df_takimlar)} takım bulundu.")
        return df_takimlar
    except Exception as e:
        print(f"Takımlar çekilirken HATA oluştu: {e}")
        return None

# --- FONKSİYON 2: Oyuncular (Değişiklik yok) ---
def oyunculari_getir():
    print("\n--- Aktif oyuncu listesi çekiliyor... ---")
    try:
        gelen_oyuncular_listesi = players.get_active_players(headers=headers)
        df_oyuncular = pd.DataFrame(gelen_oyuncular_listesi)
        print(f"Toplam {len(df_oyuncular)} aktif oyuncu bulundu.")
        return df_oyuncular
    except Exception as e:
        print(f"Oyuncular çekilirken HATA oluştu: {e}")
        return None

# --- FONKSİYON 3: Takım Maçları (Değişiklik yok) ---
def maclari_getir(sezon):
    print(f"\n--- {sezon} Sezonu [Takım] Maçları Çekiliyor... ---")
    try:
        finder = leaguegamefinder.LeagueGameFinder(season_nullable=sezon, headers=headers)
        df_maclar = finder.get_data_frames()[0]
        print(f"Toplam {len(df_maclar)} [takım] maç kaydı bulundu.")
        return df_maclar
    except Exception as e:
        print(f"Maçlar {sezon} çekilirken HATA oluştu: {e}")
        return None

# --- FONKSİYON 4: Oyuncu Sezon İstatistikleri (Değişiklik yok) ---
def oyuncu_sezon_istatistiklerini_getir(sezon):
    print(f"\n--- {sezon} Sezonu [Oyuncu SEZON] İstatistikleri Çekiliyor... ---")
    try:
        stats = leaguedashplayerstats.LeagueDashPlayerStats(season=sezon, headers=headers)
        df_istatistikler = stats.get_data_frames()[0]
        print(f"Toplam {len(df_istatistikler)} oyuncunun sezon istatistiği bulundu.")
        return df_istatistikler
    except Exception as e:
        print(f"İstatistikler {sezon} çekilirken HATA oluştu: {e}")
        return None

# --- FONKSİYON 5: Oyuncu Maç Maç Performansları (Değişiklik yok) ---
def oyuncu_mac_performanslarini_getir(sezon):
    print(f"\n--- {sezon} Sezonu [Oyuncu MAÇ MAÇ] Performansları Çekiliyor... ---")
    print("(Bu işlem en uzun süren işlemdir, binlerce satır çekilecek...)")
    try:
        # 'Player' (P) loglarını istiyoruz, 'Team' (T) değil.
        gamelogs = leaguegamelog.LeagueGameLog(season=sezon, player_or_team_abbreviation='P', headers=headers)
        df_gamelogs = gamelogs.get_data_frames()[0]
        print(f"Toplam {len(df_gamelogs)} [oyuncu-maç] performansı bulundu.")
        return df_gamelogs
    except Exception as e:
        print(f"Oyuncu maç performansları {sezon} çekilirken HATA oluştu: {e}")
        return None


# ==================================================
# --- ANA PROGRAM (Verileri Buluta Kaydet) ---
# ==================================================
print("\nNBA Veri Güncelleme Programı Başladı.")
print(f"Hedef Sezonlar: {GECEN_SEZON} ve {BU_SEZON}")
print("Veriler Bulut (Neon) Veritabanına kaydedilecek...") # <-- Güncellendi

# 1. Takımlar ve Oyuncular (Hızlı)
try:
    df_tum_takimlar = takimlari_getir()
    if df_tum_takimlar is not None:
        df_tum_takimlar.to_sql('takimlar', con=engine, if_exists='replace', index=False)
        print(">>> Takım verileri veritabanına kaydedildi.")
    time.sleep(1) 

    df_tum_oyuncular = oyunculari_getir()
    if df_tum_oyuncular is not None:
        df_tum_oyuncular.to_sql('oyuncular', con=engine, if_exists='replace', index=False)
        print(">>> Aktif oyuncu listesi veritabanına kaydedildi.")
    time.sleep(1)

    # 2. Takım Maçları (Orta Hızda)
    print("\n--- [Takım] Maç Verileri İşleniyor ---")
    df_maclar_gecen_sezon = maclari_getir(GECEN_SEZON)
    time.sleep(1)
    df_maclar_bu_sezon = maclari_getir(BU_SEZON)

    if df_maclar_gecen_sezon is not None and df_maclar_bu_sezon is not None:
        df_tum_maclar = pd.concat([df_maclar_gecen_sezon, df_maclar_bu_sezon])
        df_tum_maclar.to_sql('maclar', con=engine, if_exists='replace', index=False)
        print(f">>> Toplam {len(df_tum_maclar)} [takım] maç kaydı veritabanına kaydedildi.")
    elif df_maclar_gecen_sezon is not None:
        df_maclar_gecen_sezon.to_sql('maclar', con=engine, if_exists='replace', index=False)
        print(f">>> Sadece {GECEN_SEZON} sezonu ({len(df_maclar_gecen_sezon)} [takım] maç) kaydedildi.")
    else:
        print("UYARI: Takım maç verileri çekilemedi.")
    time.sleep(1)

    # 3. Oyuncu Sezon İstatistikleri (Orta Hızda)
    print("\n--- [Oyuncu SEZON] İstatistikleri İşleniyor ---")
    df_stats_gecen_sezon = oyuncu_sezon_istatistiklerini_getir(GECEN_SEZON)
    time.sleep(1)
    df_stats_bu_sezon = oyuncu_sezon_istatistiklerini_getir(BU_SEZON)

    if df_stats_gecen_sezon is not None and df_stats_bu_sezon is not None:
        df_tum_istatistikler = pd.concat([df_stats_gecen_sezon, df_stats_bu_sezon])
        df_tum_istatistikler.to_sql('oyuncu_sezon_istatistikleri', con=engine, if_exists='replace', index=False)
        print(f">>> Toplam {len(df_tum_istatistikler)} [oyuncu-sezon] istatistik kaydı veritabanına kaydedildi.")
    elif df_stats_gecen_sezon is not None:
        df_stats_gecen_sezon.to_sql('oyuncu_sezon_istatistikleri', con=engine, if_exists='replace', index=False)
        print(f">>> Sadece {GECEN_SEZON} sezonu ({len(df_stats_gecen_sezon)} istatistik) kaydedildi.")
    else:
        print("UYARI: Oyuncu sezon istatistikleri çekilemedi.")
    time.sleep(1)

    # 4. Oyuncu Maç Performansları (YAVAŞ)
    print("\n--- [Oyuncu MAÇ MAÇ] Performansları İşleniyor ---")
    df_gamelogs_gecen_sezon = oyuncu_mac_performanslarini_getir(GECEN_SEZON)
    time.sleep(1) 
    df_gamelogs_bu_sezon = oyuncu_mac_performanslarini_getir(BU_SEZON)

    if df_gamelogs_gecen_sezon is not None and df_gamelogs_bu_sezon is not None:
        df_tum_gamelogs = pd.concat([df_gamelogs_gecen_sezon, df_gamelogs_bu_sezon])
        # NOT: Tablo adı 'oyuncu_mac_performanslari' olmalı, app.py'nin okuduğuyla aynı.
        df_tum_gamelogs.to_sql('oyuncu_mac_performanslari', con=engine, if_exists='replace', index=False)
        print(f">>> Toplam {len(df_tum_gamelogs)} [oyuncu-maç] performansı ({GECEN_SEZON} ve {BU_SEZON}) veritabanına kaydedildi.")
    elif df_gamelogs_gecen_sezon is not None:
        df_gamelogs_gecen_sezon.to_sql('oyuncu_mac_performanslari', con=engine, if_exists='replace', index=False)
        print(f">>> Sadece {GECEN_SEZON} sezonu ({len(df_gamelogs_gecen_sezon)} performans) kaydedildi.")
    else:
        print("UYARI: Oyuncu maç performansları çekilemedi.")

    print("\n--- TÜM VERİ ÇEKME VE KAYDETME İŞLEMLERİ TAMAMLANDI ---")
    print(f"Verileriniz (TÜM DETAYLAR) artık Bulut Veritabanınızda (Neon).")

except Exception as e:
    print(f"\n--- ANA PROGRAMDA BÜYÜK HATA OLUŞTU ---")
    print(f"Hata: {e}")
    # Hata durumunda app.py'nin bunu bilmesi için script'in hata vermesini sağla
    raise e

# ===================================================================
# === CSV'YE AKTARMA BÖLÜMÜ TAMAMEN SİLİNDİ ===
# === (Artık app.py doğrudan Neon'dan okuyacak) ===
# ===================================================================