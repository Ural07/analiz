import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta 
import time
from nba_api.stats.endpoints import scoreboardv2 # Bu import artık kullanılmıyor ama kalsın
import traceback
# 'requests' ve 'BeautifulSoup' import'ları kaldırıldı.

# ========================================================================
# === ANALYZE_STREAKS (Sürüm 3.7 - Ortalamaya Dönüş Mantığı) ===
# (Bu fonksiyonda değişiklik yok)
# ========================================================================
def analyze_streaks(data, threshold_col, threshold_val):
    if data.empty or threshold_col not in data.columns:
        return "Veri yok", 0, 0, 0.0, 0, 0.0, "Veri Yok", "", "", "", "Veri Yok", 0.0, 0.0, 0

    data_cleaned = data.copy()
    data_cleaned = data_cleaned[pd.to_numeric(data_cleaned[threshold_col], errors='coerce').notna()]
    data_cleaned = data_cleaned[np.isfinite(data_cleaned[threshold_col])]
    
    if data_cleaned.empty or len(data_cleaned) < 3:
        return "Veri yok", 0, 0, 0.0, 0, 0.0, "Yetersiz Temiz Veri", "", "", "", "Veri Yok", 0.0, 0.0, 0

    data_cleaned['above'] = data_cleaned[threshold_col] >= threshold_val
    raw_pattern_list = ["ATTI" if x else "ATAMADI" for x in data_cleaned['above']]
    raw_pattern = "-".join(raw_pattern_list)
    
    total_matches = len(data_cleaned)
    total_above = data_cleaned['above'].sum()
    total_below = total_matches - total_above

    data_cleaned['group'] = (data_cleaned['above'] != data_cleaned['above'].shift()).cumsum()
    streaks = data_cleaned.groupby('group').agg(
        is_above=('above', 'first'),
        count=('above', 'size')
    )
    
    if streaks.empty:
        return raw_pattern, total_matches, total_above, 0.0, total_below, 0.0, "Yetersiz seri", "", "", "", "Veri Yok", 0.0, 0.0, 0
    
    current_streak = streaks.iloc[-1]
    historical_streaks = streaks.iloc[:-1] 
    
    above_streaks = historical_streaks[historical_streaks['is_above'] == True]['count']
    below_streaks = historical_streaks[historical_streaks['is_above'] == False]['count']
    
    avg_above_streak = above_streaks.mean() if not above_streaks.empty else 0.0
    avg_below_streak = below_streaks.mean() if not below_streaks.empty else 0.0

    comment_header = "MEVCUT YORUM VE OLASILIK:\n"
    comment_body = ""
    prob_header = ""
    prob_body = ""
    prob_break_pct_float = 0.0 
    current_type_str = "Veri Yok"
    avg_streak = 0.0
    
    current_length = int(current_streak['count'])
    current_type = current_streak['is_above']
    
    if current_type == True:
        current_type_str = "eşik üstü ('ATTI')" 
        next_type_str = "eşik altına düşme ('ATAMADI')"
        all_streaks_of_type = above_streaks 
        avg_streak = avg_above_streak
    else:
        current_type_str = "eşik altı ('ATAMADI')" 
        next_type_str = "eşiği geçme ('ATTI')"
        all_streaks_of_type = below_streaks 
        avg_streak = avg_below_streak

    comment_body += f"  Mevcut Durum: {current_length} maçlık bir {current_type_str} serisi devam ediyor.\n"
    
    if avg_streak > 0:
        comment_body += f"  Tarihsel Ortalama: Tamamlanmış serilerin ortalama uzunluğu {avg_streak:.2f} maçtır.\n"
        if current_length > avg_streak:
            comment_body += f"  Yorum: Mevcut seri, tarihsel ortalamasından daha uzun sürüyor.\n"
        else:
            comment_body += f"  Yorum: Mevcut seri, henüz tarihsel ortalama uzunluğuna ulaşmamış.\n"
    else:
        comment_body += "  Yorum: Karşılaştırma için yeterli tarihsel seri verisi yok.\n"
        
    N_reached = (all_streaks_of_type >= current_length).sum()
    N_continued = (all_streaks_of_type > current_length).sum()
    
    prob_header = "  OLASILIK TAHMİNİ:\n"
    
    if N_reached == 0:
        reversion_signal_found = False
        if not historical_streaks.empty:
            previous_streak = historical_streaks.iloc[-1]
            prev_type = previous_streak['is_above']
            prev_length = int(previous_streak['count'])

            if current_type == False and prev_type == True: 
                if avg_above_streak > 0 and prev_length >= (avg_above_streak * 2):
                    prob_break_pct_float = 75.0 
                    reversion_signal_found = True
                    prob_body = (f"    >> ORTALAMAYA DÖNÜŞ SİNYALİ! (Güçlü ÜST Sinyali)\n"
                                 f"    >> Oyuncu, ortalamanın ({avg_above_streak:.1f} maç) çok üzerinde ({prev_length} maç) bir 'ATTI' serisinden sonra sadece 1 maç 'ATAMADI'.\n"
                                 f"    >> Yüksek olasılıkla (%{prob_break_pct_float:.1f}) normale dönüp 'ATTI' serisine geri başlayacaktır.")
            
            elif current_type == True and prev_type == False: 
                if avg_below_streak > 0 and prev_length >= (avg_below_streak * 2):
                    prob_break_pct_float = 75.0 
                    reversion_signal_found = True
                    prob_body = (f"    >> ORTALAMAYA DÖNÜŞ SİNYALİ! (Güçlü ALT Sinyali)\n"
                                 f"    >> Oyuncu, ortalamanın ({avg_below_streak:.1f} maç) çok üzerinde ({prev_length} maç) bir 'ATAMADI' serisinden sonra sadece 1 maç 'ATTI'.\n"
                                 f"    >> Yüksek olasılıkla (%{prob_break_pct_float:.1f}) normale dönüp 'ATAMADI' serisine geri başlayacaktır.")

        if not reversion_signal_found:
            prob_break_pct_float = 1.0 
            prob_body += f"    >> Tarihsel veride bu uzunlukta ({current_length} maç) tamamlanmış bir seriye hiç rastlanmadı.\n"
            prob_body += f"    >> Mevcut seri tarihsel bir rekor olabilir, bu nedenle geçmişe dayalı olasılık hesaplanamaz.\n"
            prob_body += f"    >> Sıralamada geri düşmesi için olasılık %{prob_break_pct_float:.1f} olarak ayarlandı."
    
    else:
        prob_break = (N_reached - N_continued) / N_reached
        prob_break_pct_float = prob_break * 100 
        
        prob_body += f"    >> Tarihsel analiz: Bu tip seriler {N_reached} kez {current_length} maç uzunluğuna ulaştı ve\n"
        prob_body += f"       bunların %{prob_break_pct_float:.1f} kadarı bir sonraki maçta KIRILDI.\n"
        prob_body += f"    >> Bir sonraki maçta serinin kırılarak '{next_type_str}' olasılığı: %{prob_break_pct_float:.1f}\n"

    return (raw_pattern, total_matches, total_above, avg_above_streak, 
            total_below, avg_below_streak, 
            comment_header, comment_body, prob_header, prob_body,
            current_type_str, prob_break_pct_float, avg_streak, int(current_length)) 

# ========================================================================
# (analyze_wl_streaks, analyze_team_logic - Değişiklik Yok)
# ========================================================================
def analyze_wl_streaks(data):
    if data.empty:
        return "Veri yok", 0.0, 0.0
    data = data.dropna(subset=['WL'])
    if data.empty:
        return "Veri yok (WL)", 0.0, 0.0
    data['is_win'] = data['WL'] == 'W'
    data['group'] = (data['is_win'] != data['is_win'].shift()).cumsum()
    streaks = data.groupby('group').agg(is_win=('is_win', 'first'), count=('is_win', 'size'))
    win_streaks = streaks[streaks['is_win'] == True]['count']
    loss_streaks = streaks[streaks['is_win'] == False]['count']
    avg_win = win_streaks.mean() if not win_streaks.empty else 0.0
    avg_loss = loss_streaks.mean() if not loss_streaks.empty else 0.0
    
    if streaks.empty:
        return "Seri verisi yok", 0.0, 0.0
        
    current_streak = streaks.iloc[-1]
    if current_streak['is_win']:
        summary = f"Mevcut seri: {current_streak['count']} maçtır KAZANIYOR."
    else:
        summary = f"Mevcut seri: {current_streak['count']} maçtır KAYBEDİYOR."
    return summary, avg_win, avg_loss

def analyze_team_logic(team_name, threshold, df_takim_mac):
    report_lines = [] 
    team_mac_data = df_takim_mac[df_takim_mac['TEAM_NAME'] == team_name].sort_values(by='GAME_DATE')
    total_team_matches = len(team_mac_data)

    report_lines.append(f"ANALİZ: {team_name.upper()}")
    report_lines.append(f"Kaynak: (Geçmiş Veriler) ({total_team_matches} maç kaydı bulundu)")
    report_lines.append("="*50 + "\n")

    if team_mac_data.empty:
        report_lines.append("Bu takım için maç verisi bulunamadı.\n")
        return "\n".join(report_lines)
    elif len(team_mac_data) < 3:
         report_lines.append("Analiz için yetersiz maç verisi (en az 3 maç gerekli).\n\n")
         return "\n".join(report_lines)

    report_lines.append(f"SAYI EŞİK ANALİZİ (Eşik: {threshold} PTS)")
    report_lines.append("-"*50)
    
    (raw_pattern, total_m, total_above, avg_a_streak, total_below, avg_b_streak,
     comment_h, comment_b, prob_h, prob_b,
     _, _, _, _
     ) = analyze_streaks(team_mac_data, 'PTS', threshold)
    
    report_lines.append(f"EŞİK ÜSTÜ (>= {threshold} PTS) SONUÇLARI:")
    report_lines.append(f"  TAKIM {total_m} MAÇTA {total_above} KERE EŞİĞİ GEÇMİŞ.")
    report_lines.append(f"  Tamamlanmış 'Attı' serilerinin ortalama uzunluğu: {avg_a_streak:.2f} maç.\n")
    report_lines.append(f"EŞİK ALTI (< {threshold} PTS) SONUÇLARI:")
    report_lines.append(f"  TAKIM {total_m} MAÇTA {total_below} KERE EŞİĞİN ALTINDA KALMIŞ.")
    report_lines.append(f"  Tamamlanmış 'Atamadı' serilerinin ortalama uzunluğu: {avg_b_streak:.2f} maç.\n")

    report_lines.append("="*50)
    report_lines.append(comment_h.strip())
    report_lines.append(comment_b.strip())
    report_lines.append(prob_h.strip())
    report_lines.append(prob_b.strip())
    report_lines.append("="*50 + "\n")

    report_lines.append(f"Tüm Maçlar Deseni ({total_m} Maçlık Kayıt):")
    pattern_wrapped = ""
    for i, part in enumerate(raw_pattern.split('-')):
        pattern_wrapped += part + "-"
        if (i + 1) % 20 == 0:
                pattern_wrapped += "\n"
    report_lines.append(f"  {pattern_wrapped.strip('-')}\n")

    report_lines.append("GALİBİYET / MAĞLUBİYET (W/L) ANALİZİ")
    report_lines.append("-"*50)
    wl_summary, avg_win, avg_loss = analyze_wl_streaks(team_mac_data)
    report_lines.append(f"MEVCUT DURUM:")
    report_lines.append(f"  {wl_summary}\n")
    report_lines.append(f"ORTALAMA SERİLER:")
    report_lines.append(f"  Tamamlanmış Galibiyet Serisi Ortalaması: {avg_win:.2f} maç")
    report_lines.append(f"  Tamamlanmış Mağlubiyet Serisi Ortalaması: {avg_loss:.2f} maç")
    return "\n".join(report_lines)

# ========================================================================
# --- GÜNCELLEME 2: 'analyze_player_logic' (Oyuncu Analizi Sekmesi) ---
# ========================================================================
def analyze_player_logic(player_name, middle_barem, df_oyuncu_mac, df_oyuncu_sezon, ANALYSIS_RANGE):
    BASE_CONFIDENCE = 50.0
    VOLUME_WEIGHT_POSITIVE = 30.0  
    VOLUME_WEIGHT_NEGATIVE = -35.0 
    EFFICIENCY_WEIGHT = 15.0       

    player_mac_data = df_oyuncu_mac[df_oyuncu_mac['PLAYER_NAME'] == player_name].sort_values(by='GAME_DATE')
    # <--- GÜNCELLEME: Rapor için toplam maç sayısını al
    total_match_count = len(player_mac_data) 
    # --- BİTTİ ---

    player_all_seasons_sorted = df_oyuncu_sezon[
        (df_oyuncu_sezon['PLAYER_NAME'] == player_name) &
        (df_oyuncu_sezon['GP'] > 0) 
    ].sort_values(by='GP', ascending=True) 

    if len(player_mac_data) < 3 or player_all_seasons_sorted.empty:
        return "HATA: Bu oyuncu için yetersiz veri (maç < 3 veya sezon verisi yok).", []
    
    bu_sezon = player_all_seasons_sorted.iloc[0] 
    gp_bs = bu_sezon['GP'] 
    
    if gp_bs == 0:
        return "HATA: Oyuncunun sezon istatistiği (GP=0) bulunamadı.", []

    s_avg_pts = bu_sezon['PTS'] / gp_bs
    s_fga = bu_sezon['FGA']
    s_fgm = bu_sezon['FGM']
    s_avg_fg_pct = s_fgm / s_fga if s_fga > 0 else 0.0
    
    # <--- GÜNCELLEME: Rapor için Takım ve Süre bilgilerini al
    team_abbr = bu_sezon.get('TEAM_ABBREVIATION', '???')
    avg_min_this_season = bu_sezon['MIN'] / gp_bs
    # --- BİTTİ ---

    barems_to_analyze = [
        middle_barem - ANALYSIS_RANGE,
        middle_barem,
        middle_barem + ANALYSIS_RANGE
    ]
    
    analysis_results = []
    report_string_list = [] 
    
    report_string_list.append(f"ANALİZ: {player_name.upper()} ({team_abbr})") # <--- GÜNCELLEME: Takım adı eklendi
    report_string_list.append(f"Orta Barem: {middle_barem} PTS (Aralık: +/- {ANALYSIS_RANGE:.1f} PTS)")
    report_string_list.append("="*50 + "\n")
    report_string_list.append("ARALIK ANALİZİ (Desen + Hacim + Verimlilik)")
    report_string_list.append("(Not: Bu analiz B2B ve Güncel Sakatlık (Delta) içermez)")
    report_string_list.append("-"*50 + "\n")
    report_string_list.append(f"Analiz ediliyor: {barems_to_analyze[0]:.1f}, {barems_to_analyze[1]:.1f}, {barems_to_analyze[2]:.1f} baremleri...")
    
    for threshold_pts in barems_to_analyze:
        if threshold_pts <= 0: continue

        (pts_pattern, _, _, _, _, _, 
         pts_comment_h, pts_comment_b, pts_prob_h, pts_prob_b, 
         pts_current_type_str, pts_prob_break_pct, _, pts_current_length
         ) = analyze_streaks(player_mac_data, 'PTS', threshold_pts)
        
        (fg_pattern, _, _, _, _, _, _, _, _, _, 
         fg_current_type_str, fg_prob_break_pct, _, _
         ) = analyze_streaks(player_mac_data, 'FG_PCT', s_avg_fg_pct)

        aday_yonu = "ÜST" if pts_current_type_str == "eşik altı ('ATAMADI')" else "ALT"
        
        final_confidence = BASE_CONFIDENCE
        comment_hacim = "NÖTR (Hacim bareme yakın)"
        comment_verimlilik = "NÖTR (Verimlilik serisi yok/etkisiz)"
        hacim_result = 0 

        hacim_skoru = s_avg_pts 
        hacim_fark = hacim_skoru - threshold_pts
        hacim_pozitif_esik = 2.0 
        hacim_negatif_esik = -2.0 

        if aday_yonu == "ÜST":
            if hacim_fark > hacim_pozitif_esik: 
                hacim_result = 1
                comment_hacim = f"POZİTİF (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f})"
            elif hacim_fark < hacim_negatif_esik: 
                hacim_result = -1
                comment_hacim = f"NEGATİF! (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f}) HACİM YETERSİZ!"
        elif aday_yonu == "ALT":
            if hacim_fark > hacim_pozitif_esik: 
                hacim_result = -1 
                comment_hacim = f"NEGATİF! (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f}) HACİM ÇOK YÜKSEK!"
            elif hacim_fark < hacim_negatif_esik: 
                hacim_result = 1 
                comment_hacim = f"POZİTİF (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f}) HACİM ZATEN DÜŞÜK!"
        
        if hacim_result == 1:
            final_confidence += VOLUME_WEIGHT_POSITIVE
        elif hacim_result == -1:
            final_confidence += VOLUME_WEIGHT_NEGATIVE 

        verimlilik_yonu = "ÜST" if fg_current_type_str == "eşik altı ('ATAMADI')" else "ALT"
        
        if fg_prob_break_pct > 1:
            if aday_yonu == verimlilik_yonu:
                final_confidence += EFFICIENCY_WEIGHT
                comment_verimlilik = "POZİTİF (FG% serisi de aynı yönde)"
            elif aday_yonu != verimlilik_yonu:
                final_confidence -= EFFICIENCY_WEIGHT
                comment_verimlilik = "NEGATİF (FG% serisi zıt yönde)"

        final_confidence = max(5, min(99, int(final_confidence)))
        full_pts_comment = (pts_prob_h + pts_prob_b).replace("\n", " ").replace("    >> ", "")
        
        sinerji_skoru = (pts_prob_break_pct / 100.0) * (final_confidence / 100.0)
        
        # <--- GÜNCELLEME: Rapor için ekstra verileri sözlüğe ekle
        analysis_results.append({
            'sinerji_skoru': sinerji_skoru,
            'name': player_name,
            'threshold': threshold_pts,
            'direction': aday_yonu,
            'confidence': final_confidence,
            'pts_prob': pts_prob_break_pct,
            'pts_comment': full_pts_comment, 
            'comment_hacim': comment_hacim,
            'comment_verimlilik': comment_verimlilik,
            'team_abbr': team_abbr,                 # <--- EKLENDİ
            'total_match_count': total_match_count, # <--- EKLENDİ
            'avg_min': avg_min_this_season          # <--- EKLENDİ
        })
        # --- BİTTİ ---
        
    report_string_list.append("\n" + "="*50 + "\n")
    report_string_list.append("ARALIK ANALİZİ SONUÇLARI (Sinerjiye Göre Sıralı)")
    report_string_list.append("="*50 + "\n")
    
    all_adaylar = sorted(
        analysis_results, 
        key=lambda x: (x['sinerji_skoru'], x['pts_prob'], x['confidence']), 
        reverse=True 
    )

    # <--- GÜNCELLEME: Sonuç raporlamasını güncelle
    for i, aday in enumerate(all_adaylar, 1):
        report_string_list.append(f"#{i}: {aday['name']} ({aday['team_abbr']}) - ({aday['threshold']:.1f} PTS {aday['direction']})")
        report_string_list.append(f"     (Veri: {aday['total_match_count']} Maç | Ort. Süre: {aday['avg_min']:.1f} dk)")
        report_string_list.append(f"  -> SİNERJİ SKORU: {aday['sinerji_skoru']:.3f}")
        report_string_list.append(f"     (Desen: %{aday['pts_prob']:.1f} | Güven: %{aday['confidence']})")
        report_string_list.append(f"  -> Desen Yorumu: {aday['pts_comment']}")
        report_string_list.append(f"  -> Hacim Yorumu: {aday['comment_hacim']}")
        report_string_list.append(f"  -> Verimlilik Yorumu: {aday['comment_verimlilik']}\n")
    # --- BİTTİ ---
    
    return "\n".join(report_string_list), all_adaylar


# ========================================================================
# === GÜNCELLEME 1: 'get_players_for_hybrid_analysis' (Rookie Filtresi) ===
# ========================================================================

def get_players_for_hybrid_analysis(
    df_games_today,        
    df_oyuncu_mac,
    df_oyuncu_sezon,
    nba_team_id_to_abbr,   
    df_injury_report       
    ):
    """
    GÜNCELLEME: Fikstür 'df_games_today' (json) üzerinden okunuyor.
    Sakatlıklar 'df_injury_report' (csv) üzerinden okunuyor.
    YENİ: 80 maçtan az verisi olan oyuncular (rookie) filtreleniyor.
    """
    
    report_lines = [] 
    TOP_N_PLAYERS_PER_TEAM = 5
    
    team_ids_playing = set()
    game_id_to_matchup_str = {}
    team_to_opponent_map = {} 
    team_to_game_map = {}
    csv_inactive_player_names = set() 
    
    try:
        report_lines.append(f"Analiz başladı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        # --- 2. FİSTÜRÜ HAFIZADAN (df_games_today) ÇEK ---
        report_lines.append(f"Hafızadaki (df_games_today) verisi işleniyor...")

        if df_games_today.empty:
            report_lines.append(f"Hafızadaki 'df_games_today' (games_today.json) boş.")
            report_lines.append(f"   -> Lütfen lokalden 'python3 fikstur_cek.py' ve 'python3 db_gonder.py' çalıştırın.")
            report_lines.append(f"   -> Ardından 'Veri Güncelle' sayfasından 'Yenile' butonuna basın.")
            return report_lines, None, today_str, None, None 

        if 'GAME_DATE_EST' in df_games_today.columns:
            try:
                today_str = pd.to_datetime(df_games_today['GAME_DATE_EST'].iloc[0]).strftime('%Y-%m-%d')
                report_lines.append(f"Fikstür tarihi olarak {today_str} (EST) belirlendi.")
            except Exception:
                pass 

        # --- 3. HARİTALARI DOLDUR ---
        report_lines.append(f"Fikstür bulundu. {len(df_games_today)} maç bulundu.")

        for index, game_row in df_games_today.iterrows():
            try:
                game_id = game_row['GAME_ID']
                team_a_id = game_row['HOME_TEAM_ID']
                team_b_id = game_row['VISITOR_TEAM_ID']
                team_a_name = game_row['HOME_TEAM']
                team_b_name = game_row['AWAY_TEAM']
                matchup_str = f"{team_b_name} @ {team_a_name}" 
                
                game_id_to_matchup_str[game_id] = matchup_str
                team_ids_playing.add(team_a_id)
                team_ids_playing.add(team_b_id)
                team_to_game_map[team_a_id] = game_id
                team_to_game_map[team_b_id] = game_id
                team_to_opponent_map[team_a_id] = team_b_id
                team_to_opponent_map[team_b_id] = team_a_id
            
            except KeyError as e:
                report_lines.append(f"UYARI: 'games_today.json' dosyasında beklenen kolon yok: {e}")
                report_lines.append(f"   -> 'fikstur_cek.py' script'ini (ID'leri ekleyen) güncellediğinizden emin olun.")
                continue 
            except Exception as e:
                report_lines.append(f"UYARI: Maç işlenirken hata: {e}")

        if not team_ids_playing:
            report_lines.append("HATA: Fikstür bulundu ancak takım ID'leri işlenemedi.")
            return report_lines, None, today_str, None, None
            
        # --- 4. SAKATLIKLARI HAFIZADAN (df_injury_report) OKU ---
        if df_injury_report.empty:
            report_lines.append("UYARI: 'nba-injury-report.csv' dosyası bulunamadı veya boş.")
            report_lines.append("   (Sakatlık filtresi ve Delta analizi CSV olmadan çalışmayacak.)")
            csv_inactive_player_names = set()
        else:
            if 'Player' in df_injury_report.columns:
                csv_inactive_player_names = set(df_injury_report['Player'].dropna())
                report_lines.append(f"CSV Sakatlık Raporu: {len(csv_inactive_player_names)} oyuncu 'nba-injury-report.csv' dosyasından (hafızadan) bulundu.")
            else:
                report_lines.append("UYARI: 'nba-injury-report.csv' dosyası yüklendi ancak 'Player' kolonu bulunamadı.")
                csv_inactive_player_names = set()
        
        report_lines.append("="*60)
        report_lines.append(f"Oynayacak {len(team_ids_playing)} takımın oyuncuları veritabanından (nba_analiz.db) aranıyor...")

        # 2. Adım: Kilit Oyuncuları 'df_oyuncu_sezon'dan Bul
        report_lines.append(f"Her takımın en çok süre alan (TOP {TOP_N_PLAYERS_PER_TEAM}) oyuncusu listelenecek...")
 
        if df_oyuncu_sezon.empty:
            report_lines.append(f"HATA: 'oyuncu_sezon_istatistikleri' (nba_analiz.db) tablosu boş.")
            return report_lines, None, today_str, None, None 

        active_player_ids = set(df_oyuncu_sezon[df_oyuncu_sezon['GP'] >= 3]['PLAYER_ID'])
        
        if not active_player_ids:
            report_lines.append(f"HATA: 3'ten fazla maç (GP >= 3) oynamış kimse bulunamadı.")
            return report_lines, None, today_str, None, None 

        report_lines.append(f"Filtre: {len(active_player_ids)} aktif oyuncu (GP >= 3) bulundu.")
        
        all_season_players_df = df_oyuncu_sezon.copy()
        
        all_season_players_df_sorted = all_season_players_df.sort_values(
            by=['PLAYER_ID', 'GP'], 
            ascending=[True, True]
        )
        current_season_players_df = all_season_players_df_sorted.drop_duplicates(
            subset=['PLAYER_ID'], 
            keep='first'
        )
        
        if csv_inactive_player_names:
            total_before_filter = len(current_season_players_df)
            current_season_players_df = current_season_players_df[
                ~current_season_players_df['PLAYER_NAME'].isin(csv_inactive_player_names)
            ]
            total_after_filter = len(current_season_players_df)
            if total_before_filter > total_after_filter:
                report_lines.append(f"CSV Sakatlık Filtresi: {total_before_filter - total_after_filter} oyuncu (CSV raporu) listeden çıkarıldı.")

        key_players_df = current_season_players_df[
            current_season_players_df['TEAM_ID'].isin(team_ids_playing)
        ].copy()
        
        if key_players_df.empty:
            report_lines.append("Hata: Fikstürdeki takımlar (games_today.json), 'oyuncu_sezon_istatistikleri' (nba_analiz.db) dosyanızdaki hiçbir oyuncuyla eşleşmedi.")
            return report_lines, None, today_str, None, None 

        # <--- GÜNCELLEME 1 (ROOKIE FİLTRESİ) BURADA BAŞLIYOR ---
        if df_oyuncu_mac.empty:
            report_lines.append("UYARI: 'oyuncu_mac_performanslari' (nba_analiz.db) tablosu boş.")
            report_lines.append("   -> Rookie (80+ maç) filtresi uygulanamayacak.")
        else:
            # 1. Tüm oyuncuların toplam maç sayısını hesapla
            match_counts = df_oyuncu_mac['PLAYER_ID'].value_counts()
            
            # 2. Bu maç sayılarını 'key_players_df' listesindeki oyuncularla eşleştir
            key_players_df['total_matches'] = key_players_df['PLAYER_ID'].map(match_counts).fillna(0).astype(int)
            
            # 3. Filtreyi uygula
            original_count = len(key_players_df)
            key_players_df = key_players_df[key_players_df['total_matches'] >=50].copy()
            filtered_count = len(key_players_df)
            
            if original_count > filtered_count:
                report_lines.append(f"ROOKIE FİLTRESİ: {original_count - filtered_count} oyuncu, 50 maçtan az veriye sahip olduğu için listeden çıkarıldı.")
            else:
                report_lines.append("ROOKIE FİLTRESİ: Tüm oyuncular 50 maç barajını geçti.")
        
        if key_players_df.empty:
            report_lines.append("Hata: Rookie filtresinden (50+ maç) sonra analiz edilecek oyuncu kalmadı.")
            return report_lines, None, today_str, None, None 
        # <--- GÜNCELLEME 1 (ROOKIE FİLTRESİ) BİTTİ ---

        
        key_players_df['MIN_PER_GAME'] = key_players_df.apply(
            lambda row: row['MIN'] / row['GP'] if row['GP'] > 0 else 0,
            axis=1
        )
        
        top_players = key_players_df.sort_values(by=['TEAM_ABBREVIATION', 'MIN_PER_GAME'], ascending=[True, False])
        top_players_grouped = top_players.groupby('TEAM_ABBREVIATION').head(TOP_N_PLAYERS_PER_TEAM).reset_index()
        
        top_players_grouped['GAME_ID'] = top_players_grouped['TEAM_ID'].map(team_to_game_map)
        top_players_grouped['MATCHUP'] = top_players_grouped['GAME_ID'].map(game_id_to_matchup_str)
        top_players_grouped['OPPONENT_TEAM_ID'] = top_players_grouped['TEAM_ID'].map(team_to_opponent_map) # B2B için
        
        game_id_to_teams = df_games_today.set_index('GAME_ID')[['HOME_TEAM', 'AWAY_TEAM']].to_dict('index')
        
        def get_team_names(row):
            game_info = game_id_to_teams.get(row['GAME_ID'], {})
            return game_info.get('HOME_TEAM', 'Bilinmeyen'), game_info.get('AWAY_TEAM', 'Bilinmeyen')
        
        top_players_grouped[['HOME_TEAM', 'AWAY_TEAM']] = top_players_grouped.apply(get_team_names, axis=1, result_type='expand')

        
        top_players_final = top_players_grouped.sort_values(
            by=['GAME_ID', 'TEAM_ID', 'MIN_PER_GAME'], 
            ascending=[True, True, False]
        )
        
        report_lines.append(f"Toplam {len(top_players_final)} kilit ve deneyimli oyuncu (80+ maç) bulundu.")
        
        return report_lines, top_players_final, today_str, current_season_players_df, csv_inactive_player_names

    except Exception as e:
        report_lines.append(f"KRİTİK HATA: {e}")
        report_lines.append(f"Hata Detayı: {traceback.format_exc()}")
        return report_lines, None, None, None, None


# ========================================================================
# === GÜNCELLEME 2: 'run_full_analysis_logic' (Ana Sayfa Analizi) ===
# ========================================================================
def run_full_analysis_logic(
    baremler, 
    top_players_final, 
    current_season_players_df, 
    csv_inactive_player_names, 
    df_oyuncu_mac,
    df_takim_mac, 
    ANALYSIS_RANGE,
    MINIMUM_PATTERN_PROBABILITY,
    today_str
    ):
    
    KEY_PLAYERS_PER_TEAM = 3 
    BASE_CONFIDENCE = 50.0
    VOLUME_WEIGHT_POSITIVE = 30.0  
    VOLUME_WEIGHT_NEGATIVE = -35.0 
    EFFICIENCY_WEIGHT = 15.0       
    B2B_WEIGHT = 15.0              
    USAGE_DELTA_WEIGHT = 25.0      
    
    report_lines = []
    analysis_results = []
    
    today_date_obj = datetime.strptime(today_str, '%Y-%m-%d').date()
    yesterday_date_obj = today_date_obj - timedelta(days=1)
            
    for (player_name, middle_barem) in baremler:
        
        barems_to_analyze = [
            middle_barem - ANALYSIS_RANGE,
            middle_barem,
            middle_barem + ANALYSIS_RANGE
        ]
        
        try:
            player_mac_data = df_oyuncu_mac[df_oyuncu_mac['PLAYER_NAME'] == player_name].sort_values(by='GAME_DATE')
            # <--- GÜNCELLEME: Rapor için toplam maç sayısını al
            total_match_count = len(player_mac_data)
            # --- BİTTİ ---
            
            player_sezon_row = top_players_final[top_players_final['PLAYER_NAME'] == player_name].iloc[0]
        except IndexError:
             report_lines.append(f"\n! {player_name} için veri bulunamadı (Indext Hatası). Atlanıyor...")
             continue

        if len(player_mac_data) < 3:
            report_lines.append(f"\n! {player_name} için yetersiz maç verisi (maç < 3). Atlanıyor...")
            continue 
        
        player_id = player_sezon_row['PLAYER_ID']
        team_id = player_sezon_row['TEAM_ID']
        opponent_team_id = player_sezon_row['OPPONENT_TEAM_ID']
        game_id = player_sezon_row['GAME_ID'] 
        gp = player_sezon_row['GP']

        if gp == 0:
            report_lines.append(f"\n! {player_name} için GP=0, analiz atlanıyor (ZeroDivisionError önlendi).")
            continue
        
        s_avg_pts = player_sezon_row['PTS'] / gp
        s_fga = player_sezon_row['FGA']
        s_fgm = player_sezon_row['FGM']
        s_avg_fg_pct = s_fgm / s_fga if s_fga > 0 else 0.0
        
        # <--- GÜNCELLEME: Rapor için Takım ve Süre bilgilerini al
        team_abbr = player_sezon_row.get('TEAM_ABBREVIATION', '???')
        avg_min_this_season = player_sezon_row.get('MIN_PER_GAME', 0.0) # Bu, get_players'da hesaplanmıştı
        # --- BİTTİ ---

        # B2B verisini BİR KEZ hesapla
        player_played_yesterday = not df_takim_mac[
            (df_takim_mac['TEAM_ID'] == team_id) & 
            (df_takim_mac['GAME_DATE'].dt.date == yesterday_date_obj)
        ].empty
        opponent_played_yesterday = not df_takim_mac[
            (df_takim_mac['TEAM_ID'] == opponent_team_id) & 
            (df_takim_mac['GAME_DATE'].dt.date == yesterday_date_obj)
        ].empty
        
        # Delta verisini BİR KEZ hesapla
        team_top_players = current_season_players_df[
            current_season_players_df['TEAM_ID'] == team_id
        ].sort_values(by='FGA', ascending=False).head(KEY_PLAYERS_PER_TEAM)
        kilit_oyuncu_isimleri = set(team_top_players['PLAYER_NAME'])
        kilit_oyuncu_isimleri.discard(player_name) 
        
        bugun_sakat_kilit_oyuncular_isimleri = kilit_oyuncu_isimleri.intersection(csv_inactive_player_names)
        
        baseline_sakat_kilit_oyuncular_isimleri = set()
        
        for index, kilit_oyuncu in team_top_players.iterrows():
            if kilit_oyuncu['PLAYER_NAME'] in kilit_oyuncu_isimleri:
                if kilit_oyuncu['GP'] < (gp / 2):
                    baseline_sakat_kilit_oyuncular_isimleri.add(kilit_oyuncu['PLAYER_NAME'])

        yeni_sakatlar_isimleri = bugun_sakat_kilit_oyuncular_isimleri.difference(baseline_sakat_kilit_oyuncular_isimleri)
        donen_oyuncular_isimleri = baseline_sakat_kilit_oyuncular_isimleri.difference(bugun_sakat_kilit_oyuncular_isimleri)
        
        delta_etkisi = 0 
        delta_oyuncu_ismi = ""
        
        if len(yeni_sakatlar_isimleri) > 0:
            delta_etkisi = 1
            delta_oyuncu_ismi = list(yeni_sakatlar_isimleri)[0] 
        elif len(donen_oyuncular_isimleri) > 0:
            delta_etkisi = -1
            delta_oyuncu_ismi = list(donen_oyuncular_isimleri)[0] 

        # Şimdi 3 barem için iç döngü
        for threshold_pts in barems_to_analyze:
            if threshold_pts <= 0: continue 
            
            (pts_pattern, _, _, _, _, _, 
             pts_comment_h, pts_comment_b, pts_prob_h, pts_prob_b, 
             pts_current_type_str, pts_prob_break_pct, _, pts_current_length
             ) = analyze_streaks(player_mac_data, 'PTS', threshold_pts)
            
            aday_yonu = "ÜST" if pts_current_type_str == "eşik altı ('ATAMADI')" else "ALT"
            aday_tag = 'buyuk_yesil' if aday_yonu == "ÜST" else 'buyuk_kirmizi'
            
            (fg_pattern, _, _, _, _, _, _, _, _, _, 
             fg_current_type_str, fg_prob_break_pct, _, _
             ) = analyze_streaks(player_mac_data, 'FG_PCT', s_avg_fg_pct)
            verimlilik_yonu = "ÜST" if fg_current_type_str == "eşik altı ('ATAMADI')" else "ALT"

            final_confidence = BASE_CONFIDENCE
            comment_hacim = "NÖTR (Hacim bareme yakın)"
            comment_verimlilik = "NÖTR (Verimlilik serisi yok/etkisiz)"
            comment_b2b = "NÖTR (B2B durumu eşit)"
            comment_delta = "NÖTR (Kadroda Delta Yok)"
            hacim_result = 0 

            if fg_prob_break_pct > 1: 
                if aday_yonu == verimlilik_yonu:
                    final_confidence += EFFICIENCY_WEIGHT
                    comment_verimlilik = f"POZİTİF (FG% serisi de {verimlilik_yonu} yönünde)"
                elif aday_yonu != verimlilik_yonu:
                    final_confidence -= EFFICIENCY_WEIGHT
                    comment_verimlilik = f"NEGATİF (FG% serisi zıt yönde ({verimlilik_yonu}))"
            
            hacim_skoru = s_avg_pts 
            hacim_fark = hacim_skoru - threshold_pts
            hacim_pozitif_esik = 2.0 
            hacim_negatif_esik = -2.0 

            if aday_yonu == "ÜST":
                if hacim_fark > hacim_pozitif_esik: 
                    hacim_result = 1
                    comment_hacim = f"POZİTİF (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f})"
                elif hacim_fark < hacim_negatif_esik: 
                    hacim_result = -1
                    comment_hacim = f"NEGATİF! (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f}) HACİM YETERSİZ!"
            elif aday_yonu == "ALT":
                if hacim_fark > hacim_pozitif_esik: 
                    hacim_result = -1 
                    comment_hacim = f"NEGATİF! (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f}) HACİM ÇOK YÜKSEK!"
                elif hacim_fark < hacim_negatif_esik: 
                    hacim_result = 1 
                    comment_hacim = f"POZİTİF (Ort: {hacim_skoru:.1f} / Barem: {threshold_pts:.1f}) HACİM ZATEN DÜŞÜK!"
            
            if hacim_result == 1:
                final_confidence += VOLUME_WEIGHT_POSITIVE
            elif hacim_result == -1:
                final_confidence += VOLUME_WEIGHT_NEGATIVE 

            if player_played_yesterday and not opponent_played_yesterday:
                final_confidence -= B2B_WEIGHT
                comment_b2b = f"NEGATİF (Oyuncu Yorgun, Rakip Dinlenmiş)"
            elif not player_played_yesterday and opponent_played_yesterday:
                final_confidence += B2B_WEIGHT
                comment_b2b = f"POZİTİF (Oyuncu Dinlenmiş, Rakip Yorgun)"

            if aday_yonu == "ÜST":
                if delta_etkisi == 1: 
                    if hacim_result == -1: final_confidence -= VOLUME_WEIGHT_NEGATIVE 
                    final_confidence += USAGE_DELTA_WEIGHT
                    comment_delta = f"POZİTİF (Hacim Artışı! {delta_oyuncu_ismi} oynamıyor)"
                elif delta_etkisi == -1: 
                    if hacim_result == 1: final_confidence -= VOLUME_WEIGHT_POSITIVE 
                    final_confidence -= USAGE_DELTA_WEIGHT
                    comment_delta = f"NEGATİF (Hacim Düşüşü! {delta_oyuncu_ismi} dönüyor)"
            elif aday_yonu == "ALT":
                if delta_etkisi == 1: 
                    final_confidence -= USAGE_DELTA_WEIGHT
                    comment_delta = f"NEGATİF (Hacim Artışı! {delta_oyuncu_ismi} oynamıyor)"
                elif delta_etkisi == -1: 
                    final_confidence += USAGE_DELTA_WEIGHT
                    comment_delta = f"POZİTİF (Hacim Düşüşü! {delta_oyuncu_ismi} dönüyor)"
            
            final_confidence = max(5, min(99, int(final_confidence)))
            full_pts_comment = (pts_prob_h + pts_prob_b).replace("\n", " ").replace("    >> ", "")
            sinerji_skoru = (pts_prob_break_pct / 100.0) * (final_confidence / 100.0)

            # <--- GÜNCELLEME: Rapor için ekstra verileri sözlüğe ekle
            analysis_results.append({
                'sinerji_skoru': sinerji_skoru,
                'game_id': game_id, 
                'player_id': player_id, 
                'name': player_name,
                'threshold': threshold_pts,
                'direction': aday_yonu,
                'tag': aday_tag,
                'confidence': final_confidence,
                'pts_prob': pts_prob_break_pct,
                'pts_streak_len': pts_current_length,
                'pts_comment': full_pts_comment, 
                'comment_hacim': comment_hacim,
                'comment_verimlilik': comment_verimlilik,
                'raw_s_avg_pts': s_avg_pts,
                'raw_s_avg_fg_pct': s_avg_fg_pct,
                'raw_b2b_comment': comment_b2b,
                'raw_delta_comment': comment_delta,
                'delta_tag': 'delta_plus' if delta_etkisi == 1 else ('delta_minus' if delta_etkisi == -1 else 'kucuk_desen'),
                'team_abbr': team_abbr,                 # <--- EKLENDİ
                'total_match_count': total_match_count, # <--- EKLENDİ
                'avg_min': avg_min_this_season          # <--- EKLENDİ
            })
            # --- BİTTİ ---
            
    report_lines.append(f"Analiz tamamlandı. Toplam {len(analysis_results)} adet barem/aday bulundu.")
    
    if not analysis_results:
         report_lines.append("Analiz edilecek sonuç bulunamadı.")
         return "\n".join(report_lines), [], []
    
    report_lines.append("Sıralama: 1. (Desen * Güven), 2. Desen, 3. Güven")
    
    all_adaylar = sorted(
        analysis_results, 
        key=lambda x: (x['sinerji_skoru'], x['pts_prob'], x['confidence']),
        reverse=True 
    )
    
    if all_adaylar:
        top_score_tuple = (
            all_adaylar[0]['sinerji_skoru'], 
            all_adaylar[0]['pts_prob'], 
            all_adaylar[0]['confidence']
        )
        tie_group = []
        other_adaylar = []
        for aday in all_adaylar:
            aday_score_tuple = (
                aday['sinerji_skoru'], 
                aday['pts_prob'], 
                aday['confidence']
            )
            if aday_score_tuple == top_score_tuple:
                tie_group.append(aday)
            else:
                other_adaylar.append(aday)
        
        if len(tie_group) > 1:
            report_lines.append(f"UYARI: En üst sırada {len(tie_group)} barem BİREBİR AYNI skora sahip.")
            report_lines.append("   -> Bu eşit grup rastgele karıştırılıyor (PRNG)...")
            random.shuffle(tie_group)
        
        all_adaylar = tie_group + other_adaylar
            
    
    top_2_diverse_picks = []
    seen_game_ids_for_top2 = set()
    
    for aday in all_adaylar:
        if aday['pts_prob'] < MINIMUM_PATTERN_PROBABILITY:
            continue 
        
        game_id = aday.get('game_id', None)
        if game_id not in seen_game_ids_for_top2:
            top_2_diverse_picks.append(aday)
            seen_game_ids_for_top2.add(game_id)
        
        if len(top_2_diverse_picks) == 2:
            break
    
    if not top_2_diverse_picks:
         report_lines.append(f"Desen Olasılığı >= %{MINIMUM_PATTERN_PROBABILITY} olan farklı maçlardan aday bulunamadı.")
    
    report_lines.append("\n" + "="*60)
    report_lines.append("EN GÜVENİLİR 2 ÖNERİ (Farklı Maçlardan)")
    report_lines.append("="*60)

    # <--- GÜNCELLEME: Sonuç raporlamasını güncelle
    for i, aday in enumerate(top_2_diverse_picks, 1):
        report_lines.append(f"\nADAY #{i} - SİNERJİ SKORU: {aday['sinerji_skoru']:.3f}")
        report_lines.append(f"  (Desen: %{aday['pts_prob']:.1f} | Güven: %{aday['confidence']})")
        report_lines.append(f"  OYUNCU: {aday['name']} ({aday['team_abbr']}) - ({aday['threshold']:.1f} PTS {aday['direction']})")
        report_lines.append(f"     (Veri: {aday['total_match_count']} Maç | Ort. Süre: {aday['avg_min']:.1f} dk)")
        report_lines.append("-" * 50)
        report_lines.append(f"  1. DESEN (PTS): {aday['pts_comment']}")
        report_lines.append(f"  2. HACİM (Sezon Ort.): {aday['comment_hacim']}")
        report_lines.append(f"  3. VERİMLİLİK (FG%): {aday['comment_verimlilik']}")
        report_lines.append(f"  4. YORGUNLUK (B2B): {aday['raw_b2b_comment']}")
        report_lines.append(f"  5. KADRO DELTASI: {aday['raw_delta_comment']}")
    # --- BİTTİ ---

    report_lines.append("\n" + "="*60)
    report_lines.append("Analiz tamamlandı.")
    
    return "\n".join(report_lines), top_2_diverse_picks, all_adaylar

# ========================================================================
# === BACKTEST LOGIC (Sürüm 5.0) ===
# (Bu fonksiyonda değişiklik yok)
# ========================================================================
def run_backtest_logic(log_data, df_mac_results, min_prob):
    total_predictions = 0
    total_success = 0
    top_4_predictions = 0
    top_4_success = 0
    
    top_4_diverse = []
    seen_games_top4 = set()
    other_results = []
    
    for aday in log_data:
        game_id = aday.get('game_id', None)
        
        if aday['pts_prob'] >= min_prob:
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
    
    report_lines_top4 = []
    if not top_4_diverse:
        report_lines_top4.append("Bu tarih için Top 4 öneri bulunamamış.\n")
    
    for i, aday in enumerate(top_4_diverse, 1):
        player_id = aday['player_id']
        game_id = aday['game_id']
        barem = aday['threshold']
        direction = aday['direction'] 
        pts_prob = aday['pts_prob']
        confidence = aday['confidence']
        
        actual_row = df_mac_results.loc[
            (df_mac_results['PLAYER_ID'] == player_id) & 
            (df_mac_results['GAME_ID'] == game_id)
        ]
        
        result_str = "SONUÇ BİLİNMİYOR (Maç CSV'de bulunamadı)"
        status = 'bilinmiyor'
        
        if not actual_row.empty:
            actual_pts = actual_row['PTS'].values[0]
            total_predictions += 1
            top_4_predictions += 1
            
            is_success = False
            if direction == "ÜST" and actual_pts >= barem:
                is_success = True
            elif direction == "ALT" and actual_pts < barem:
                is_success = True
            
            if is_success:
                total_success += 1
                top_4_success += 1
                status = 'basarili'
                result_str = f"BAŞARILI (Sonuç: {actual_pts:.0f} PTS)"
            else:
                status = 'basarisiz'
                result_str = f"BAŞARISIZ (Sonuç: {actual_pts:.0f} PTS)"
        
        # <--- GÜNCELLEME: Backtest raporuna da takım adını ekleyelim (eğer logda varsa)
        team_abbr_str = f"({aday.get('team_abbr', '???')})" # .get() kullanarak eski logların hata vermesini engelle
        report_lines_top4.append( (f"#{i}: {aday['name']} {team_abbr_str} ({barem:.1f} {direction}) [D: {pts_prob:.0f}% | G: {confidence}%] -> {result_str}", status) )

    report_lines_other = []
    if not other_results:
        report_lines_other.append( ("Listede başka analiz bulunmuyor.\n", 'kucuk_desen') )
        
    for i, aday in enumerate(other_results, len(top_4_diverse) + 1):
        player_id = aday['player_id']
        game_id = aday['game_id']
        barem = aday['threshold']
        direction = aday['direction'] 
        pts_prob = aday['pts_prob']
        confidence = aday['confidence']
        
        actual_row = df_mac_results.loc[
            (df_mac_results['PLAYER_ID'] == player_id) & 
            (df_mac_results['GAME_ID'] == game_id)
        ]
        
        result_str = "SONUÇ BİLİNMİYOR"
        status = 'bilinmiyor'
        
        if not actual_row.empty:
            actual_pts = actual_row['PTS'].values[0]
            total_predictions += 1 
            
            is_success = False
            if direction == "ÜST" and actual_pts >= barem:
                is_success = True
            elif direction == "ALT" and actual_pts < barem:
                is_success = True
            
            if is_success:
                total_success += 1 
                status = 'basarili'
                result_str = f"BAŞARILI (Sonuç: {actual_pts:.0f} PTS)"
            else:
                status = 'basarisiz'
                result_str = f"BAŞARISIZ (Sonuç: {actual_pts:.0f} PTS)"
        
        filter_str = "(FİLTREYE TAKILDI)" if aday['pts_prob'] < min_prob else ""
        
        # <--- GÜNCELLEME: Backtest raporuna da takım adını ekleyelim (eğer logda varsa)
        team_abbr_str = f"({aday.get('team_abbr', '???')})"
        report_lines_other.append( (f"#{i}: {aday['name']} {team_abbr_str} ({barem:.1f} {direction}) [D: {pts_prob:.0f}% | G: {confidence}%] {filter_str} -> {result_str}", status) )

    report_summary = []
    
    if top_4_predictions == 0:
        report_summary.append( (f"Top 4 Öneri Başarısı: %0.0 (0/0)", 'buyuk_kirmizi') )
    else:
        top_4_rate = (top_4_success / top_4_predictions) * 100
        report_summary.append( (f"Top 4 Öneri Başarısı: %{top_4_rate:.1f} ({top_4_success}/{top_4_predictions})", 'buyuk_yesil') )
        
    if total_predictions == 0:
        report_summary.append( (f"Tüm Analizler Başarısı: %0.0 (0/0)", 'buyuk_kirmizi') )
    else:
        total_rate = (total_success / total_predictions) * 100
        report_summary.append( (f"Tüm Analizler Başarısı: %{total_rate:.1f} ({total_success}/{total_predictions})", 'buyuk_yesil') )

    return report_lines_top4, report_lines_other, report_summary, (top_4_success, top_4_predictions, total_success, total_predictions)