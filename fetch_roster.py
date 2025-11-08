import os
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import analysis_engine  # Ana "beyin" dosyamızı içe aktarıyoruz

print("GÜNLÜK OYUNCU ÇEKME (CRON JOB) BAŞLADI...")

# 1. Veritabanı Bağlantısı (app.py ve veri_cek.py'deki gibi)
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL is None:
    print("KRİTİK HATA: .env dosyasında DATABASE_URL bulunamadı.")
    exit()
try:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    print("Bulut (Neon) Veritabanı bağlantısı başarılı.")
except Exception as e:
    print(f"KRİTİK HATA: Bulut veritabanına bağlanılamadı: {e}")
    exit()

# 2. Gerekli Verileri Belleğe Yükle (analysis_engine'in ihtiyacı var)
try:
    print("Analiz için 'oyuncu_mac' ve 'oyuncu_sezon' yükleniyor...")
    df_oyuncu_sezon = pd.read_sql_query("SELECT * FROM oyuncu_sezon_istatistikleri", con=engine)

    # app.py'den: NBA API Haritasını Yükle
    team_data = df_oyuncu_sezon[['TEAM_ID', 'TEAM_ABBREVIATION']].drop_duplicates()
    nba_team_id_to_abbr = pd.Series(
        team_data['TEAM_ABBREVIATION'].values, 
        index=team_data['TEAM_ID']
    ).to_dict()
except Exception as e:
    print(f"HATA: Gerekli tablolar yüklenemedi: {e}")
    exit()

# 3. YAVAŞ OLAN API ÇAĞRISINI YAP
# Bu, app.py'nin yapmaya çalışıp başaramadığı iş.
# API zaman aşımını 300 saniye (5 dakika) olarak ayarlıyoruz.
print("NBA API'den günün fikstürü çekiliyor (Bu işlem yavaştır)...")
try:
    (report_lines, 
     top_players_final, 
     today_str, 
     current_season_players_df, 
     csv_inactive_player_names) = analysis_engine.get_players_for_hybrid_analysis(
            df_oyuncu_sezon, nba_team_id_to_abbr, timeout_seconds=800
     )

    if top_players_final is None:
        print("HATA: analysis_engine oyuncu listesi döndürmedi. Rapor:")
        print("\n".join(report_lines))
        exit()

except Exception as e:
    print(f"KRİTİK HATA: 'get_players_for_hybrid_analysis' çalışırken çöktü: {e}")
    exit()

# 4. Sonucu "Buzdolabına" (Yeni Tablo) Yaz
try:
    print(f"Başarılı. {len(top_players_final)} oyuncu bulundu. Veritabanına yazılıyor...")

    # 'top_players_final' (bir DataFrame) veritabanına 'gunluk_oyuncular' adıyla yazılır.
    # if_exists='replace' -> Eski listeyi siler, yenisini yazar.
    top_players_final.to_sql('gunluk_oyuncular', con=engine, if_exists='replace', index=False)

    print("BAŞARILI: 'gunluk_oyuncular' tablosu bulut veritabanında güncellendi.")
    print("GÜNLÜK OYUNCU ÇEKME (CRON JOB) BİTTİ.")

except Exception as e:
    print(f"KRİTİK HATA: Sonuçlar veritabanına ('gunluk_oyuncular') yazılamadı: {e}")
    exit()