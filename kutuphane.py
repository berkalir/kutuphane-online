 import psycopg2
import os
from nicegui import ui
import hashlib
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from nicegui import app
import smtplib
from email.message import EmailMessage
import random
import datetime

app.add_static_files('/static', 'static')

# --- 1. Veritabanı Fonksiyonları ---

DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://canatalay:canatalay.374@localhost:5432/kutuphane")
# SQLAlchemy ile bağlantı kurarken bu adresi kullanın

def get_connection():
    return psycopg2.connect(
        dbname="kutuphane",
        user="canatalay",
        password="canatalay.374",
        host="localhost",   # <--- Docker Compose'da servis adı db ama normal bilgisayarda localhost!
        port="5432"
    )

def veritabani_olustur():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (
        id SERIAL PRIMARY KEY,
        isim TEXT,
        email TEXT UNIQUE,
        sifre TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS kitaplar (
        id SERIAL PRIMARY KEY,
        ad TEXT,
        yazar TEXT,
        yayinevi TEXT,
        basim_yili INTEGER
    )''')
    # Ödünç kitaplar tablosu
    cursor.execute('''CREATE TABLE IF NOT EXISTS odunc_kitaplar (
        id SERIAL PRIMARY KEY,
        kullanici_id INTEGER REFERENCES kullanicilar(id),
        kitap_id INTEGER REFERENCES kitaplar(id),
        alis_tarihi DATE,
        teslim_tarihi DATE,
        teslim_edildi BOOLEAN DEFAULT FALSE,
        teslim_edilme_tarihi DATE
    )''')
    conn.commit()
    conn.close()

def hash_sifre(sifre: str) -> str:
    return hashlib.sha256(sifre.encode('utf-8')).hexdigest()

def kullanici_ekle(isim, email, sifre):
    try:
        sifre_hashli = hash_sifre(sifre)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO kullanicilar (isim, email, sifre) VALUES (%s, %s, %s)', (isim, email, sifre_hashli))
        conn.commit()
        conn.close()
        return (True, 'Kayıt başarılı!')
    except psycopg2.IntegrityError:
        return (False, 'Bu e-posta zaten kullanılıyor!')
    except Exception as e:
        return (False, f'Hata: {e}')

def kullanici_dogrula(email, sifre):
    sifre_hashli = hash_sifre(sifre)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM kullanicilar WHERE email = %s AND sifre = %s', (email, sifre_hashli))
    kullanici = cursor.fetchone()
    conn.close()
    return kullanici is not None

def kitap_ekle(ad, yazar, yayinevi, basim_yili):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO kitaplar (ad, yazar, yayinevi, basim_yili) VALUES (%s, %s, %s, %s)',
                   (ad, yazar, yayinevi, basim_yili))
    conn.commit()
    conn.close()

def kitap_guncelle(kitap_id, ad, yazar, yayinevi, basim_yili):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''UPDATE kitaplar SET ad = %s, yazar = %s, yayinevi = %s, basim_yili = %s WHERE id = %s''',
                   (ad, yazar, yayinevi, basim_yili, kitap_id))
    conn.commit()
    conn.close()

def kitaplari_getir():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, ad, yazar, yayinevi, basim_yili FROM kitaplar ORDER BY id DESC')
    rows = cursor.fetchall()
    if rows:
        columns = [desc[0] for desc in cursor.description]
        kitaplar = [dict(zip(columns, row)) for row in rows]
    else:
        kitaplar = []
    conn.close()
    return kitaplar

def kitap_sil(kitap_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM kitaplar WHERE id = %s', (kitap_id,))
    conn.commit()
    conn.close()

def admin_kullanicisi_olustur():
    isim = "Admin"
    email = "canatalay374@gmail.com"
    sifre = "canatalay.374" # Güvenli bir şifre kullanın, gerçek uygulamada hash'leyin!
    sifre_hashli = hash_sifre(sifre)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM kullanicilar WHERE email = %s', (email,))
    if cursor.fetchone() is None:
        try:
            cursor.execute('INSERT INTO kullanicilar (isim, email, sifre) VALUES (%s, %s, %s)', (isim, email, sifre_hashli))
            conn.commit()
            print("Admin kullanıcısı oluşturuldu.")
        except psycopg2.IntegrityError:
            print("Admin kullanıcısı zaten mevcut.")
    conn.close()

def tum_kullanicilari_getir():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, isim, email, sifre FROM kullanicilar ORDER BY id ASC')  # sifre eklendi
    rows = cursor.fetchall()
    if rows:
        columns = [desc[0] for desc in cursor.description]
        kullanicilar = [dict(zip(columns, row)) for row in rows]
    else:
        kullanicilar = []
    conn.close()
    return kullanicilar

def kullanici_sil_db(kullanici_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM kullanicilar WHERE id = %s', (kullanici_id,))
    conn.commit()
    conn.close()

def eski_sifreleri_hashle():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, sifre FROM kullanicilar')
    users = cursor.fetchall()
    for user_id, sifre in users:
        # Eğer şifre zaten hash'li ise (64 karakter ve sadece hex karakterler içeriyorsa) atla
        if len(sifre) == 64 and all(c in '0123456789abcdef' for c in sifre):
            continue
        yeni_hash = hash_sifre(sifre)
        cursor.execute('UPDATE kullanicilar SET sifre = %s WHERE id = %s', (yeni_hash, user_id))
    conn.commit()
    conn.close()
    print('Tüm eski şifreler hash\'lendi.')

# --- Ödünç Alma Fonksiyonları ---

def odunc_al(kullanici_id, kitap_id):
    alis_tarihi = datetime.date.today()
    teslim_tarihi = alis_tarihi + datetime.timedelta(days=20)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''INSERT INTO odunc_kitaplar (kullanici_id, kitap_id, alis_tarihi, teslim_tarihi, teslim_edildi)
                      VALUES (%s, %s, %s, %s, FALSE)''',
                   (kullanici_id, kitap_id, alis_tarihi, teslim_tarihi))
    conn.commit()
    conn.close()

def teslim_et(odunc_id):
    teslim_edilme_tarihi = datetime.date.today()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''UPDATE odunc_kitaplar SET teslim_edildi = TRUE, teslim_edilme_tarihi = %s WHERE id = %s''',
                   (teslim_edilme_tarihi, odunc_id))
    conn.commit()
    conn.close()

def kullanicinin_oduncleri(kullanici_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''SELECT o.id, k.ad, k.yazar, o.alis_tarihi, o.teslim_tarihi, o.teslim_edildi, o.teslim_edilme_tarihi
                      FROM odunc_kitaplar o
                      JOIN kitaplar k ON o.kitap_id = k.id
                      WHERE o.kullanici_id = %s
                      ORDER BY o.alis_tarihi DESC''', (kullanici_id,))
    rows = cursor.fetchall()
    if rows:
        columns = [desc[0] for desc in cursor.description]
        oduncler = [dict(zip(columns, row)) for row in rows]
    else:
        oduncler = []
    conn.close()
    return oduncler

def geciken_oduncler():
    today = datetime.date.today()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''SELECT o.id, k.ad, k.yazar, u.isim, u.email, o.alis_tarihi, o.teslim_tarihi
                      FROM odunc_kitaplar o
                      JOIN kitaplar k ON o.kitap_id = k.id
                      JOIN kullanicilar u ON o.kullanici_id = u.id
                      WHERE o.teslim_edildi = FALSE AND o.teslim_tarihi < %s''', (today,))
    rows = cursor.fetchall()
    if rows:
        columns = [desc[0] for desc in cursor.description]
        gecikenler = [dict(zip(columns, row)) for row in rows]
    else:
        gecikenler = []
    conn.close()
    return gecikenler

# --- Geciken kitaplar için otomatik e-posta gönderme fonksiyonu ---
def geciken_kullanicilara_mail_gonder():
    gecikenler = geciken_oduncler()
    # Kullanıcıya göre grupla
    kullanici_dict = {}
    for odunc in gecikenler:
        email = odunc['email']
        if email not in kullanici_dict:
            kullanici_dict[email] = {
                'isim': odunc['isim'],
                'email': email,
                'kitaplar': []
            }
        kullanici_dict[email]['kitaplar'].append({
            'ad': odunc['ad'],
            'yazar': odunc['yazar'],
            'alis_tarihi': odunc['alis_tarihi'],
            'teslim_tarihi': odunc['teslim_tarihi']
        })
    # Her kullanıcıya mail gönder
    for kullanici in kullanici_dict.values():
        kitap_listesi = ""
        for kitap in kullanici['kitaplar']:
            kitap_listesi += f"- {kitap['ad']} (Yazar: {kitap['yazar']}) | Alış Tarihi: {kitap['alis_tarihi']} | Son Teslim: {kitap['teslim_tarihi']}\n"
        mesaj = EmailMessage()
        mesaj.set_content(f"Sayın {kullanici['isim']},\n\nAşağıdaki kitap(lar)ı zamanında teslim etmediniz. Lütfen en kısa sürede kütüphaneye iade ediniz.\n\n{kitap_listesi}\n\nİyi günler dileriz.\nKütüphane Yönetimi")
        mesaj['Subject'] = 'Geciken Kitap(lar)ınız Hakkında Uyarı'
        mesaj['From'] = GMAIL_ADRES
        mesaj['To'] = kullanici['email']
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(GMAIL_ADRES, GMAIL_APP_SIFRE)
                smtp.send_message(mesaj)
            print(f"Geciken kitaplar için e-posta gönderildi: {kullanici['email']}")
        except Exception as e:
            print(f"E-posta gönderilemedi: {kullanici['email']} - Hata: {e}")

# --- Test için gecikmiş ödünç kaydı oluşturma fonksiyonu ---
def test_gecikme_olustur():
    conn = get_connection()
    cursor = conn.cursor()
    # Teslim edilmemiş en son ödünç kaydını bul
    cursor.execute("SELECT id FROM odunc_kitaplar WHERE teslim_edildi = FALSE ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        odunc_id = row[0]
        # Teslim tarihini 5 gün öncesine çek
        yeni_teslim_tarihi = datetime.date.today() - datetime.timedelta(days=5)
        cursor.execute("UPDATE odunc_kitaplar SET teslim_tarihi = %s WHERE id = %s", (yeni_teslim_tarihi, odunc_id))
        conn.commit()
        print(f"Test için gecikmiş ödünç kaydı oluşturuldu. Odunc ID: {odunc_id}")
    else:
        print("Teslim edilmemiş ödünç kaydı bulunamadı.")
    conn.close()

# --- 2. NiceGUI Arayüzü ---

ui.add_head_html("""
<style>
body {
    background: red !important;
}
</style>
""")

@ui.page('/')
def giris_sayfasi():
    ui.add_head_html("""
<style>
body {
    background: url('/static/kutuphaneresim.jpg') no-repeat center center fixed;
    background-size: cover;
}
</style>
""")
    with ui.card().classes('absolute-center w-96'):
        ui.label("Kütüphane Giriş").classes('text-2xl font-bold self-center')
        email = ui.input("E-posta Adresi").props('outlined dense')
        sifre = ui.input("Şifre", password=True, password_toggle_button=True).props('outlined dense')
        
        def giris_yap_handler():
            if not email.value or not sifre.value: 
                ui.notify("Lütfen tüm alanları doldurun.", type="warning")
                return
            
            girilen_email = email.value.strip().lower()
            girilen_sifre = sifre.value.strip()

            if kullanici_dogrula(girilen_email, girilen_sifre):
                # Kullanıcı ID'sini storage'a kaydet
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT id FROM kullanicilar WHERE email = %s', (girilen_email,))
                user_row = cursor.fetchone()
                conn.close()
                if user_row:
                    nicegui_app.storage.user['user_id'] = user_row[0]
                ui.notify("Giriş başarılı! 🎉", color='positive')
                # Admin kontrolü
                if girilen_email == "canatalay374@gmail.com" and girilen_sifre == "canatalay.374":
                    ui.navigate.to('/admin') # Admin ise admin paneline
                else:
                    ui.navigate.to('/kitaplar') # Normal kullanıcı ise kitaplar sayfasına
            else: 
                ui.notify("E-posta veya şifre hatalı.", type="negative")

        ui.button("Giriş Yap", on_click=giris_yap_handler).classes('mt-4 w-full')
        ui.button("Yeni Hesap Oluştur", on_click=lambda: ui.navigate.to('/kayit')).classes('mt-2 w-full').props('flat')

# --- 2FA için e-posta ile kod gönderme fonksiyonu ---
GMAIL_ADRES = 'atalaycan374@gmail.com'  # Buraya kendi Gmail adresini yaz
GMAIL_APP_SIFRE = 'lfdn fwdy cuss ggtt'    # Buraya uygulama şifreni yaz

def send_verification_code(email, code):
    msg = EmailMessage()
    msg.set_content(f"Kütüphane sistemine kayıt için doğrulama kodunuz: {code}")
    msg['Subject'] = 'Kayıt Doğrulama Kodu'
    msg['From'] = GMAIL_ADRES
    msg['To'] = email
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(GMAIL_ADRES, GMAIL_APP_SIFRE)
        smtp.send_message(msg)

def generate_code():
    return str(random.randint(100000, 999999))

# --- 2FA ile Kayıt Akışı ---
from nicegui import app as nicegui_app

@ui.page('/kayit')
def kayit_sayfasi():
    ui.add_head_html("""
    <style>
    body {
        background: url('/static/kutuphaneresim.jpg') no-repeat center center fixed;
        background-size: cover;
    }
    </style>
    """)
    with ui.card().classes('absolute-center w-96'):
        ui.label('Yeni Kullanıcı Kaydı').classes('text-2xl font-bold self-center')
        isim = ui.input('İsim Soyisim').props('outlined dense')
        email = ui.input('E-posta Adresi').props('outlined dense')
        sifre = ui.input('Şifre', password=True, password_toggle_button=True).props('outlined dense')
        def kayit_ol_handler():
            if not isim.value or not email.value or not sifre.value:
                ui.notify('Lütfen tüm alanları doldurun!', type='warning')
                return
            # 2FA kodu üret ve e-posta ile gönder
            kod = generate_code()
            nicegui_app.storage.user['pending_register'] = {
                'isim': isim.value.strip(),
                'email': email.value.strip().lower(),
                'sifre': sifre.value.strip(),
                'kod': kod
            }
            try:
                send_verification_code(email.value.strip().lower(), kod)
                ui.notify('Doğrulama kodu e-posta adresinize gönderildi.', color='positive')
                ui.navigate.to('/dogrulama')
            except Exception as e:
                ui.notify(f'E-posta gönderilemedi: {e}', type='negative')
        ui.button("Kaydol", on_click=kayit_ol_handler).classes("mt-4 w-full")
        ui.button("Giriş Ekranına Dön", on_click=lambda: ui.navigate.to('/')).classes('mt-2 w-full').props('flat')

# --- Doğrulama kodu girişi sayfası ---
@ui.page('/dogrulama')
def dogrulama_sayfasi():
    ui.add_head_html("""
    <style>
    body {
        background: url('/static/kutuphaneresim.jpg') no-repeat center center fixed;
        background-size: cover;
    }
    </style>
    """)
    with ui.card().classes('absolute-center w-96'):
        ui.label('E-posta Doğrulama').classes('text-2xl font-bold self-center')
        kod_input = ui.input('E-posta ile gelen 6 haneli kodu girin').props('outlined dense')
        def dogrula_handler():
            pending = nicegui_app.storage.user.get('pending_register')
            if not pending:
                ui.notify('Kayıt bilgisi bulunamadı, lütfen tekrar kayıt olun.', type='negative')
                ui.navigate.to('/kayit')
                return
            if kod_input.value == pending['kod']:
                basarili, mesaj = kullanici_ekle(pending['isim'], pending['email'], pending['sifre'])
                if basarili:
                    ui.notify('Kayıt başarılı!', color='positive')
                    nicegui_app.storage.user.pop('pending_register', None)
                    ui.navigate.to('/')
                else:
                    ui.notify(mesaj, type='negative')
            else:
                ui.notify('Kod yanlış veya süresi doldu!', type='negative')
        ui.button('Doğrula', on_click=dogrula_handler).classes('mt-4 w-full')
        ui.button('Kayıt Ekranına Dön', on_click=lambda: ui.navigate.to('/kayit')).classes('mt-2 w-full').props('flat')

@ui.page('/kitaplar')
def kitap_sayfasi():
    ui.label('📚 Kitap Yönetim Sistemi').classes('text-3xl font-bold self-center')

    # Sağ üstte butonlar
    with ui.column().classes('absolute-top-right mt-4 mr-4 items-end'):
        ui.button('Çıkış Yap', on_click=lambda: [nicegui_app.storage.user.pop('user_id', None), ui.navigate.to('/')]).props('color=negative outline')
        user_id = nicegui_app.storage.user.get('user_id')
        if user_id:
            ui.button('Aldığım Kitaplar', on_click=lambda: ui.navigate.to('/odunclerim'), color='secondary')

    with ui.row().classes('w-full items-start no-wrap'):
        # --- Kitap Ekleme Dialogu ve Butonu ---
        kitap_ekle_dialog = None
        def kitap_ekle_formu(dialog_kapat_callback=None):
            ad = ui.input('Kitap Adı')
            yazar = ui.input('Yazar')
            yayinevi = ui.input('Yayınevi')
            basim_yili = ui.number('Basım Yılı', format='%.0f')
            def kitap_ekle_handler():
                if not ad.value or not yazar.value:
                    ui.notify('Kitap adı ve yazar zorunludur.', type='warning')
                    return
                kitap_ekle(ad.value, yazar.value, yayinevi.value, basim_yili.value)
                ui.notify('Kitap başarıyla eklendi!', color='positive')
                ad.value, yazar.value, yayinevi.value, basim_yili.value = '', '', '', None
                build_kitap_listesi.refresh()
                if dialog_kapat_callback:
                    dialog_kapat_callback()
            ui.button('Kitabı Kaydet', on_click=kitap_ekle_handler)

        # Sağ: Arama ve kitap listesi
        with ui.column().classes('w-full'):
            # Arama ve kitap ekle aynı satırda
            with ui.row().classes('w-full max-w-lg mt-4 items-center'):
                arama_metni = ui.input('Kitap Ara...', on_change=lambda: build_kitap_listesi.refresh()).props('outlined dense clearable icon=search').classes('flex-grow')
                async def kitap_ekle_dialog_ac():
                    with ui.dialog() as dialog, ui.card():
                        ui.label('Yeni Kitap Ekle').classes('text-lg font-bold')
                        kitap_ekle_formu(dialog.close)
                        with ui.row().classes('justify-end'):
                            ui.button('İptal', on_click=dialog.close)
                    await dialog
                ui.button('Kitap Ekle', on_click=kitap_ekle_dialog_ac, color='primary').props('flat').classes('ml-2')

            @ui.refreshable
            def build_kitap_listesi():
                tum_kitaplar = kitaplari_getir()
                # Arama metni varsa filtreleme yap
                if arama_metni.value:
                    filtreli_kitaplar = [
                        kitap for kitap in tum_kitaplar 
                        if arama_metni.value.lower() in kitap['ad'].lower() or
                           arama_metni.value.lower() in kitap['yazar'].lower() or
                           arama_metni.value.lower() in kitap['yayinevi'].lower() or
                           arama_metni.value.lower() in str(kitap['basim_yili']).lower()
                    ]
                else:
                    filtreli_kitaplar = tum_kitaplar

                if not filtreli_kitaplar:
                    ui.label("Aradığınız kriterlere uygun kitap bulunmamaktadır." if arama_metni.value else "Henüz kayıtlı kitap bulunmamaktadır.").classes('text-center text-gray-500 mt-4')
                    return

                for kitap in filtreli_kitaplar:
                    with ui.row().classes('w-full items-center p-2 border-b'):
                        with ui.column().classes('flex-grow'):
                            ui.label(kitap['ad']).classes('font-bold')
                            ui.label(f"Yazar: {kitap['yazar']} | Yıl: {kitap['basim_yili']}").classes('text-sm text-gray-600')
                        # Ödünç Al butonu (kullanıcı giriş yaptıysa)
                        user_id = nicegui_app.storage.user.get('user_id')
                        if user_id:
                            def odunc_al_handler(k_id=kitap['id']):
                                odunc_al(user_id, k_id)
                                ui.notify('Kitap ödünç alındı! 20 gün içinde teslim ediniz.', color='positive')
                            ui.button('Ödünç Al', on_click=lambda _, k_id=kitap['id']: odunc_al_handler(k_id), color='green').props('dense')
                        async def duzenle_handler(_, k=kitap):
                            with ui.dialog() as duzenle_dialog, ui.card():
                                yeni_ad = ui.input('Kitap Adı', value=k['ad'])
                                yeni_yazar = ui.input('Yazar', value=k['yazar'])
                                yeni_yayinevi = ui.input('Yayınevi', value=k['yayinevi'])
                                yeni_yil = ui.number('Basım Yılı', value=k['basim_yili'], format='%.0f')
                                with ui.row().classes('justify-end'):
                                    ui.button('İptal', on_click=duzenle_dialog.close)
                                    def kaydet_handler():
                                        kitap_guncelle(k['id'], yeni_ad.value, yeni_yazar.value, yeni_yayinevi.value, yeni_yil.value)
                                        ui.notify('Kitap güncellendi!', color='positive')
                                        duzenle_dialog.close()
                                        build_kitap_listesi.refresh()
                                    ui.button('Kaydet', on_click=kaydet_handler, color='primary')
                            await duzenle_dialog

                        async def kitap_sil_onayla(k_id):
                            with ui.dialog() as sil_dialog, ui.card():
                                ui.label('Bu kitabı silmek istediğinize emin misiniz?')
                                with ui.row():
                                    ui.button('İptal', on_click=sil_dialog.close)
                                    def sil_ve_yenile():
                                        kitap_sil(k_id)
                                        sil_dialog.close()
                                        build_kitap_listesi.refresh()
                                    ui.button('Sil', on_click=sil_ve_yenile, color='negative')
                            await sil_dialog

                        ui.button('Düzenle', on_click=duzenle_handler, color='blue').props('dense')
                        ui.button('Sil', on_click=lambda _, k_id=kitap['id']: kitap_sil_onayla(k_id), color='red').props('dense')

            build_kitap_listesi()

    ui.button('Çıkış Yap', on_click=lambda: [nicegui_app.storage.user.pop('user_id', None), ui.navigate.to('/')]).props('color=negative outline').classes('absolute-top-right mt-4 mr-4')

@ui.page('/admin')
def admin_paneli():
    ui.label('⚙️ Admin Paneli').classes('text-3xl font-bold self-center q-mt-md')

    ui.add_head_html("""
    <style>
    .sag-ust-butons {
        position: absolute;
        top: 24px;
        right: 32px;
        z-index: 100;
        display: flex;
        gap: 12px;
    }
    </style>
    """)
    
    with ui.row().classes('sag-ust-butons'):
        ui.button('Kitap Yönetimine Dön', on_click=lambda: ui.navigate.to('/kitaplar')).props('color=primary outline')
        ui.button('Ödünç Kitap Sistemi', on_click=lambda: ui.navigate.to('/odunc-yonetim')).props('color=secondary outline')
        ui.button('Çıkış Yap', on_click=lambda: ui.navigate.to('/')).props('color=negative outline')

    # --- Gecikenlere Mail Gönder Butonu ---
    def mail_gonder_handler():
        geciken_kullanicilara_mail_gonder()
        ui.notify('Geciken kullanıcılara e-posta gönderildi (veya gönderilmeye çalışıldı).', color='positive')
    ui.button('Gecikenlere Mail Gönder', on_click=mail_gonder_handler, color='warning').classes('mb-4')

    # --- Gecikme Testi Butonu ---
    def gecikme_test_handler():
        test_gecikme_olustur()
        ui.notify('Test için gecikmiş ödünç kaydı oluşturuldu.', color='info')
     ui.button('Gecikme Testi Oluştur', on_click=gecikme_test_handler, color='secondary').classes('mb-4')

    async def kullanici_sil_onay(kullanici_id: int, kullanici_isim: str):
        with ui.dialog() as onay_dialogu, ui.card():
            ui.label('Onay Gerekiyor').classes('text-xl')
            ui.label(f"'{kullanici_isim}' adlı kullanıcıyı silmek istediğinizden emin misiniz?")
            ui.label("Bu işlem geri alınamaz!").classes('text-red-600 font-bold')
            with ui.row().classes('w-full justify-end q-mt-md'):
                ui.button('İptal', on_click=onay_dialogu.close, color='primary')
                ui.button('Sil', on_click=lambda: onay_dialogu.submit('evet'), color='negative')
        
        sonuc = await onay_dialogu
        if sonuc == 'evet':
            kullanici_email_cek_conn = get_connection()
            kullanici_email_cek_cursor = kullanici_email_cek_conn.cursor()
            kullanici_email_cek_cursor.execute('SELECT email FROM kullanicilar WHERE id = %s', (kullanici_id,))
            result = kullanici_email_cek_cursor.fetchone()
            if result is not None:
                silinecek_email = result[0]
                kullanici_email_cek_conn.close()

                if silinecek_email == "canatalay374@gmail.com":
                    ui.notify("Admin hesabını silemezsiniz!", type="negative")
                else:
                    kullanici_sil_db(kullanici_id)
                    ui.notify(f"'{kullanici_isim}' adlı kullanıcı başarıyla silindi.", color='positive')
                    kullanici_listesi_kapsayici.clear()
                    with kullanici_listesi_kapsayici:
                        build_kullanici_listesi()
            else:
                ui.notify("Kullanıcı bulunamadı.", type="warning")
                kullanici_listesi_kapsayici.clear()
                with kullanici_listesi_kapsayici:
                    build_kullanici_listesi()
            
    def build_kullanici_listesi():
        kullanicilar = tum_kullanicilari_getir()
        if not kullanicilar:
            ui.label("Henüz kayıtlı kullanıcı bulunmamaktadır.").classes('text-center text-gray-500 mt-4')
            return
            
        for kullanici in kullanicilar:
            with ui.row().classes('w-full items-center p-2 border-b'):
                with ui.column().classes('flex-grow'):
                    ui.label(f"ID: {kullanici['id']}").classes('text-sm text-gray-500')
                    ui.label(kullanici['isim']).classes('font-bold')
                    ui.label(kullanici['email']).classes('text-sm text-blue-600')
                    ui.label(f"Şifre: {kullanici['sifre']}").classes('text-sm text-red-600')  # Şifreyi göster
                
                if kullanici['email'] == "canatalay374@gmail.com":
                    ui.label("Admin Hesabı").classes('text-info text-sm')
                else:
                    ui.button('Sil', on_click=lambda _, k_id=kullanici['id'], k_isim=kullanici['isim']: kullanici_sil_onay(k_id, k_isim), color='red').props('dense')

    ui.label('Sistemdeki Kullanıcılar').classes('text-xl font-semibold q-mt-lg')
    ui.separator().classes('my-2')
    
    kullanici_listesi_kapsayici = ui.column().classes('w-2/3 max-w-lg mx-auto q-mt-md')

    # --- Kullanıcı Ekleme Dialogu ve Butonu ---
    async def kullanici_ekle_dialog():
        with ui.dialog() as ekle_dialog, ui.card():
            ui.label('Yeni Kullanıcı Ekle').classes('text-lg font-bold')
            yeni_isim = ui.input('İsim Soyisim').props('outlined dense')
            yeni_email = ui.input('E-posta Adresi').props('outlined dense')
            yeni_sifre = ui.input('Şifre', password=True, password_toggle_button=True).props('outlined dense')
            with ui.row().classes('justify-end'):
                ui.button('İptal', on_click=ekle_dialog.close)
                def ekle_handler():
                    if not yeni_isim.value or not yeni_email.value or not yeni_sifre.value:
                        ui.notify('Tüm alanları doldurun!', type='warning')
                        return
                    basarili, mesaj = kullanici_ekle(yeni_isim.value.strip(), yeni_email.value.strip().lower(), yeni_sifre.value.strip())
                    if basarili:
                        ui.notify(mesaj, color='positive')
                        ekle_dialog.close()
                        kullanici_listesi_kapsayici.clear()
                        with kullanici_listesi_kapsayici:
                            build_kullanici_listesi()
                    else:
                        ui.notify(mesaj, type='negative')
                ui.button('Ekle', on_click=ekle_handler, color='primary')
        await ekle_dialog

    ui.button('Kullanıcı Ekle', on_click=kullanici_ekle_dialog, color='primary').classes('mb-4')

    with kullanici_listesi_kapsayici:
        build_kullanici_listesi()

# --- Kullanıcının ödünç aldığı kitaplar sayfası ---
@ui.page('/odunclerim')
def odunclerim_sayfasi():
    ui.add_head_html("""
    <style>
    body {
        background: url('/static/kutuphaneresim.jpg') no-repeat center center fixed;
        background-size: cover;
    }
    .sabit-kart {
        max-width: 800px;
        width: 70vw;
        margin: 40px auto 0 auto;
        min-height: 300px;
        max-height: 600px;
        display: flex;
        flex-direction: column;
    }
    .scrollable-content {
        max-height: 350px;
        overflow-y: auto;
        margin-bottom: 16px;
    }
    </style>
    """)
    with ui.card().classes('sabit-kart'):
        ui.label('Aldığım Kitaplar').classes('text-2xl font-bold self-center mb-4')
        user_id = nicegui_app.storage.user.get('user_id')
        if not user_id:
            ui.notify('Giriş yapmalısınız!', type='warning')
            ui.navigate.to('/')
            return
        oduncler = kullanicinin_oduncleri(user_id)
        if not oduncler:
            ui.label('Şu anda ödünç aldığınız kitap yok.').classes('text-center text-gray-500 mt-4')
        else:
            ui.html('<div class="scrollable-content">')
            for odunc in oduncler:
                with ui.row().classes('w-full items-center p-2 border-b'):
                    with ui.column().classes('flex-grow'):
                        ui.label(odunc['ad']).classes('font-bold')
                        ui.label(f"Yazar: {odunc['yazar']} | Aldığım Tarih: {odunc['alis_tarihi']} | Son Teslim: {odunc['teslim_tarihi']}").classes('text-sm text-gray-600')
                        if odunc['teslim_edildi']:
                            ui.label(f"Teslim Edildi: {odunc['teslim_edilme_tarihi']}").classes('text-green-700 text-sm')
                        else:
                            kalan = (odunc['teslim_tarihi'] - odunc['alis_tarihi']).days
                            if kalan < 0:
                                ui.label('Teslim süresi geçti!').classes('text-red-600 text-sm')
                    if not odunc['teslim_edildi']:
                        def teslim_et_handler(odunc_id=odunc['id']):
                            teslim_et(odunc_id)
                            ui.notify('Kitap teslim edildi olarak işaretlendi.', color='positive')
                            ui.navigate.to('/odunclerim')
                        ui.button('Teslim Et', on_click=lambda _, odunc_id=odunc['id']: teslim_et_handler(odunc_id), color='primary').props('dense')
            ui.html('</div>')
        ui.button('Kitaplara Dön', on_click=lambda: ui.navigate.to('/kitaplar')).classes('mt-4 w-full')

# --- Admin ödünç yönetim sayfası ---
@ui.page('/odunc-yonetim')
def odunc_yonetim_sayfasi():
    import datetime
    ui.add_head_html("""
    <style>
    body {
        background: url('/static/kutuphaneresim.jpg') no-repeat center center fixed;
        background-size: cover;
    }
    .sabit-kart {
        max-width: 900px;
        width: 90vw;
        margin: 40px auto 0 auto;
        min-height: 300px;
        max-height: 600px;
        display: flex;
        flex-direction: column;
        box-sizing: border-box;
    }
    .scrollable-content {
        flex: 1 1 auto;
        min-height: 0;
        max-height: 400px;
        overflow-y: auto;
        width: 100%;
        box-sizing: border-box;
        padding-right: 8px;
    }
    </style>
    """)
    with ui.card().classes('sabit-kart'):
        ui.label('Ödünç Alınan Kitaplar').classes('text-2xl font-bold self-center mb-4')
        # Tüm ödünçler (teslim edilmiş ve edilmemiş)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT o.id, k.ad, k.yazar, u.isim, u.email, o.alis_tarihi, o.teslim_tarihi, o.teslim_edildi, o.teslim_edilme_tarihi
                          FROM odunc_kitaplar o
                          JOIN kitaplar k ON o.kitap_id = k.id
                          JOIN kullanicilar u ON o.kullanici_id = u.id
                          ORDER BY o.alis_tarihi DESC''')
        rows = cursor.fetchall()
        if rows:
            columns = [desc[0] for desc in cursor.description]
            oduncler = [dict(zip(columns, row)) for row in rows]
        else:
            oduncler = []
        conn.close()
        if not oduncler:
            ui.label('Henüz ödünç alınan kitap yok.').classes('text-center text-gray-500 mt-4')
        else:
            with ui.element('div').classes('scrollable-content'):
                for odunc in oduncler:
                    with ui.row().classes('w-full items-center p-2 border-b'):
                        with ui.column().classes('flex-grow'):
                            ui.label(f"Kitap: {odunc['ad']} (Yazar: {odunc['yazar']})").classes('font-bold')
                            ui.label(f"Kullanıcı: {odunc['isim']} ({odunc['email']})").classes('text-sm text-blue-600')
                            ui.label(f"Alış Tarihi: {odunc['alis_tarihi']} | Son Teslim: {odunc['teslim_tarihi']}").classes('text-sm text-gray-600')
                            if odunc['teslim_edildi']:
                                ui.label(f"Teslim Edildi: {odunc['teslim_edilme_tarihi']}").classes('text-green-700 text-sm')
                            else:
                                bugun = datetime.date.today()
                                if odunc['teslim_tarihi'] < bugun:
                                    ui.label('Teslim süresi geçti!').classes('text-red-600 text-sm')
                                else:
                                    kalan = (odunc['teslim_tarihi'] - bugun).days
                                    ui.label(f"Kalan gün: {kalan}").classes('text-yellow-700 text-sm')
        ui.button('Admin Paneline Dön', on_click=lambda: ui.navigate.to('/admin')).classes('mt-4 w-full')

# --- 3. Uygulamayı Başlatma ---
if __name__ in {"__main__", "__mp_main__"}:
    veritabani_olustur()
    admin_kullanicisi_olustur()
    eski_sifreleri_hashle()  # <-- Bir defa çalıştır, sonra silebilirsin
    ui.run(title="Kütüphane Sistemi", storage_secret="super-secret-key")

