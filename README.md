# Doctor-Money
# Doctor Money

Doctor Money menyediakan dashboard web dan bot Telegram di atas satu penyimpanan akun bersama. Dashboard lama menyimpan data di `localStorage`; saat dibuka lewat API, pengguna masuk ke alur pratinjau impor. Transaksi contoh `s...`, saldo/budget/tujuan bawaan yang belum diubah dilewati. Tidak ada seed data yang ditulis otomatis ke akun server.

## Persiapan

1. Buat bot melalui [@BotFather](https://t.me/BotFather), jalankan `/newbot`, lalu simpan token bot.
2. Salin `.env.example` menjadi `.env`; isi `BOT_TOKEN`, `APP_SECRET` acak minimal 32 byte, `WEBAPP_URL` HTTPS Firebase Hosting, `DATABASE_URL`, `MODE`, dan `TZ`. Jangan commit `.env`.
3. Dashboard dapat disajikan oleh Firebase Hosting; API tetap berada di service backend pada `/api`.
4. Pasang Python 3.11 atau lebih baru, lalu pasang dependensi:

	```bash
	python -m venv .venv
	. .venv/bin/activate
	pip install -r requirements.txt
	```

## Menjalankan Lokal

Set `MODE=polling`, lalu jalankan API dan bot pada dua terminal:

```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8000
python -m bot.main
```

Buka `http://127.0.0.1:8000/`. Tombol Telegram Mini App membutuhkan HTTPS publik, jadi untuk uji dari Telegram gunakan tunnel HTTPS tepercaya dan isi `WEBAPP_URL` dengan URL tunnel tersebut. Polling bot tidak membutuhkan webhook.

Dashboard tetap dapat digunakan tanpa masuk. Dalam mode lokal, transaksi dan pengaturan disimpan di browser ini saja dan tidak tersinkron ke server atau Telegram; gunakan tombol **Masuk / Daftar** di dashboard untuk menyinkronkannya. Setelah masuk atau mendaftar, data lokal dapat ditinjau sebelum diimpor ke akun. Akun email dan kata sandi (minimal 12 karakter) dapat didaftarkan langsung tanpa konfirmasi email. Akun lama tanpa email dapat menambahkan kredensial di halaman Dompet; setelah masuk, hubungkan Telegram dengan menjalankan `/hubungkan` di bot lalu masukkan kode yang berlaku 10 menit.

Lupa kata sandi? Pilih **Lupa kata sandi?** pada halaman masuk untuk meminta kode sekali pakai lewat email atau chat bot Telegram yang sudah tertaut. Kode berlaku 10 menit, maksimal 5 kali percobaan, dan penggantian kata sandi akan mengeluarkan semua sesi aktif tanpa menghapus data keuangan. Untuk pengiriman lewat email, konfigurasi `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_SSL`, dan `SMTP_STARTTLS` di environment server. Telegram memakai `BOT_TOKEN`; pengguna harus sudah menautkan bot ke akun dan pernah memulai chat bot. Jangan simpan kredensial SMTP di frontend atau commit `.env`.

Tab **Diagnose** mengikuti alur **Past → Present → Future → Action**: merangkum arus kas enam bulan, saldo dan kondisi bulan berjalan, mendeteksi defisit/konsentrasi pengeluaran/anggaran terlampaui/cadangan tipis, lalu menawarkan simulasi penghematan 0–30% untuk membandingkan proyeksi saldo 3, 6, dan 12 bulan dengan baseline. Baseline memakai hingga tiga bulan bertransaksi yang sudah selesai; bila riwayat itu belum tersedia, bulan berjalan digunakan sementara dan ditandai belum lengkap. Proyeksi mengasumsikan arus kas rata-rata berulang dan tidak memasukkan bunga, inflasi, atau perubahan pemasukan. Skor kesehatan dashboard tetap transparan: rasio tabungan (40 poin), anggaran (30 poin), dan dana darurat (30 poin); tanpa anggaran, komponen anggaran bernilai netral 15 poin. Analisis dihitung lokal dari transaksi/dompet/anggaran tersimpan dan tidak mengirim data ke layanan AI eksternal. Hasilnya adalah estimasi edukatif, bukan nasihat profesional atau jaminan saldo masa depan.

Bagian **Ke mana uang saya?** di beranda merangkum pemasukan, pengeluaran per kategori dengan proporsi visual, kategori yang paling banyak menyerap pengeluaran, dan sisa/defisit arus kas untuk bulan terpilih. Semua nominal ditampilkan dalam Rupiah (IDR). Persentase kategori dibandingkan dengan total pengeluaran (serta dibandingkan ke pemasukan sebagai konteks). Sisa arus kas berarti pemasukan dikurangi pengeluaran tercatat, bukan saldo tabungan aktual; transfer/penyetoran tabungan tidak dihitung sebagai konsumsi.

Pada pengguna browser pertama, backend membuat akun kosong. Bila `localStorage` lama ditemukan, dashboard menampilkan jumlah transaksi/dompet yang akan dipindahkan dan jumlah item contoh yang dilewati. Pilih **Impor data saya** untuk menyimpan atau **Gunakan akun kosong** untuk menyimpan cadangan lokal dan mulai dengan akun server kosong. Data contoh dashboard tidak dipulihkan pada akun yang sudah tersinkron.

## Doctor Money Pro

Akun baru mendapat uji coba Pro selama 1 bulan sejak status Pro pertama kali diperiksa. Setelah uji coba atau langganan berakhir, akun beralih ke paket Gratis: pencatatan pemasukan dan pengeluaran, dashboard dasar, riwayat transaksi, dan statistik sederhana, dengan batas 50 transaksi per bulan (dihitung per bulan tanggal transaksi; data yang sudah ada tetap bisa diubah atau dihapus). Batas ini ditegakkan API, bot Telegram, dan WhatsApp. Diagnose, Anggaran, dan Aset di dashboard hanya untuk Pro. Paket saat ini Rp20.000 untuk 1 bulan, Rp100.000 untuk 6 bulan, dan Rp180.000 untuk 1 tahun. Paket 6 bulan menghemat Rp20.000 dan paket tahunan menghemat Rp60.000 dibanding harga bulanan.

Pembayaran saat ini diverifikasi manual. Isi `PRO_PAYMENT_METHOD`, `PRO_PAYMENT_ACCOUNT`, dan `PRO_PAYMENT_ACCOUNT_NAME` untuk menampilkan instruksi transfer. Tetapkan `PRO_ADMIN_SECRET` dengan nilai rahasia yang kuat dan berbeda dari `APP_SECRET`. Order tertunda dapat dilihat melalui `GET /api/admin/pro/orders`; setelah pembayaran diverifikasi, aktifkan lewat `POST /api/admin/pro/orders/{order_id}/activate` dengan header `Authorization: Bearer $PRO_ADMIN_SECRET`. Jangan kirim secret admin ke browser atau commit `.env`.

Data entitlement dan order disimpan pada tabel `pro_access` dan `pro_orders`, yang dibuat otomatis saat API mulai. Row Level Security diaktifkan pada kedua tabel saat menggunakan PostgreSQL/Supabase; akses aplikasi tetap lewat backend.

## Menu dan Data

Menu utama Telegram menyediakan Web App dan 22 tombol fitur. Command keuangan mencakup `/catat`, `/riwayat`, `/saldo`, `/wallet`, `/transfer`, `/budget`, `/laporan`, `/grafik`, `/pengingat`, `/simulasi`, `/zona`, `/hubungkan`, dan `/hapusdata`. Command pasar mencakup `/topcrypto`, `/crypto`, `/indeks`, `/saham`, `/kurs`, `/watchlist`, `/alert`, `/portofolio`, `/emas`, dan `/feargreed`. `/alert BTC > 70000` dapat langsung membuat alert. Pengaitan memakai kode satu kali yang kedaluwarsa dalam 10 menit. Bot yang belum terhubung memakai akun lokal yang terisolasi per `telegram_id`; saat ditautkan, bot dan dashboard membaca state yang sama.

API menyimpan JSON state dashboard dengan revisi optimistis dalam database yang dikonfigurasi. Bot dan API menggunakan transaksi repository yang sama. `DATABASE_URL` menerima URL PostgreSQL standar (`postgresql://...`) maupun SQLAlchemy (`postgresql+psycopg://...`); URL PostgreSQL standar otomatis menggunakan driver `psycopg` yang disertakan. Host Supabase otomatis memakai TLS (`sslmode=require`) kecuali URL sudah menetapkan mode TLS yang lebih kuat.

## Pencatatan lewat WhatsApp

Transaksi bisa dicatat dengan mengirim pesan WhatsApp ke nomor bot. Data masuk ke akun yang sama dengan dashboard dan bot Telegram, sehingga langsung terlihat di keduanya (dashboard menyegarkan data tiap 30 detik). Gateway yang dipakai adalah [WasenderAPI](https://wasenderapi.com).

Penyiapan (sekali saja):

1. Di dashboard WasenderAPI, buat sesi WhatsApp dan scan QR dengan nomor yang akan menjadi bot. Salin **API Key** sesi tersebut.
2. Di pengaturan sesi, isi **Webhook URL** dengan `https://DOMAIN-API-ANDA/api/whatsapp/webhook`, buat **Webhook Secret**, dan aktifkan event `messages.received`.
3. Di service API (Railway), isi `WASENDER_API_KEY`, `WASENDER_WEBHOOK_SECRET`, dan `WHATSAPP_BOT_NUMBER` (nomor bot, format `62xxx`), lalu deploy ulang.
4. Tiap pengguna membuka dashboard → **Dompet → Hubungkan WhatsApp → Buat kode penautan**, lalu mengirim `hubungkan KODE` dari WhatsApp-nya ke nomor bot. Nomor yang belum tertaut tidak bisa mencatat.

Format pesan:

- `keluar 25000 makan siang`, `masuk 2jt gaji`, `keluar 50rb bensin #transport`
- Kata kunci: `keluar`/`beli`/`bayar` = pengeluaran; `masuk`/`terima`/`gaji` = pemasukan. Tanpa kata kunci, tipe ditebak seperti di bot Telegram.
- Nominal: `25000`, `25.000`, `25rb`, `25k`, `2jt`, `1,5jt`.
- Kategori dari hashtag (`#makan`, `#transport`, `#belanja`, `#tagihan`, `#hiburan`, `#kesehatan`, `#pendidikan`, `#gaji`, `#freelance`, `#bonus`) atau ditebak dari keterangan; default Lainnya.
- `saldo` = ringkasan bulan ini, `hapus` = hapus transaksi terakhir yang dicatat lewat WhatsApp, `bantuan` = daftar perintah.

Transaksi disimpan ke dompet pertama di daftar dompet. Webhook menolak permintaan tanpa header `X-Webhook-Signature` yang cocok, mengabaikan pesan grup dan pesan dari bot sendiri.

Uji webhook secara lokal (API berjalan di port 8000 dengan dua variabel `WASENDER_*` terisi):

```bash
curl -X POST http://127.0.0.1:8000/api/whatsapp/webhook \
  -H "Content-Type: application/json" -H "X-Webhook-Signature: $WASENDER_WEBHOOK_SECRET" \
  -d '{"event":"messages.received","data":{"messages":{"key":{"id":"TES1","fromMe":false,"remoteJid":"6281234567890@s.whatsapp.net","cleanedSenderPn":"6281234567890"},"messageBody":"keluar 25rb makan siang"}}}'
```

## Docker dan Produksi

```bash
docker compose up --build -d
```

Compose menjalankan API pada port 8000 dan bot pada polling dengan volume database persisten. Taruh reverse proxy HTTPS di depannya; jangan mengekspos database atau endpoint internal. Cadangkan volume `doctor-money-data` secara rutin. Untuk deployment Railway atau multi-instance, gunakan PostgreSQL.

### Deployment Railway + Supabase + Firebase

Aplikasi ini adalah backend/bot Python dengan frontend HTML statis. Railway menjalankan API dan bot; Supabase menyediakan PostgreSQL; Firebase Hosting menyajikan halaman web. Kedua service Railway harus memakai connection string Supabase yang sama supaya dashboard dan bot melihat akun serta data yang sama.

1. Buat proyek Supabase. Di **Connect**, pilih **Session pooler** (host dan username harus disalin persis dari proyek Anda; port `5432`). Ini menyediakan koneksi IPv4 yang sesuai untuk service Railway yang berjalan lama. Jangan pilih **Transaction pooler** untuk koneksi SQLAlchemy aplikasi ini.
2. Sebelum beralih database, buat backup terbaru database PostgreSQL Railway. Lalu hentikan sementara service API dan bot agar tidak ada transaksi baru selama dump/restore. Dari mesin tepercaya yang memiliki akses ke kedua database, set `RAILWAY_DATABASE_URL` ke connection string PostgreSQL Railway yang dapat diakses dari mesin tersebut (aktifkan TCP Proxy/public networking sementara bila diperlukan) dan `SUPABASE_DATABASE_URL` ke connection string Supabase Session pooler. Jangan masukkan kedua URL ke chat, source control, log, atau file repo.
3. Pastikan target Supabase adalah database baru/kosong, kemudian salin schema dan semua baris, termasuk akun, token, kode pairing, reset password, langganan, dan order:

```bash
pg_dump --format=custom --no-owner --no-acl \
  --file=doctor_money.dump "$RAILWAY_DATABASE_URL"
pg_restore --no-owner --no-acl --exit-on-error \
  --dbname="$SUPABASE_DATABASE_URL" doctor_money.dump
```

URL harus berisi password database yang benar; percent-encode karakter khusus password (misalnya `@`, `#`, `?`, atau spasi). Gunakan URL Session pooler Supabase lengkap dari dialog **Connect**, jangan menebak hostname atau username. Arsip dump memuat data finansial sensitif: simpan secara privat, jangan commit, verifikasi keberhasilan restore, lalu hapus salinan sementara dengan aman. Jangan jalankan restore ke proyek Supabase yang sudah berisi data yang perlu dipertahankan.

4. Di Railway, buat dua service aplikasi dari repo ini: API dan bot. Keduanya menggunakan konfigurasi `railway.json`/`start.sh`; set `APP_RUNTIME=api` untuk API, serta `APP_RUNTIME=bot` dan `MODE=polling` untuk bot. Atur `DATABASE_URL` pada **keduanya** ke string Session pooler Supabase yang sama. Atur juga `BOT_TOKEN`, `APP_SECRET` yang kuat dan sama pada kedua service, `WEBAPP_URL=https://doctor-moneys.web.app`, dan `TZ=Asia/Jakarta`. Tambahkan variabel `PRO_*`, SMTP, atau `COINGECKO_API_KEY` hanya jika fitur terkait digunakan. Jangan set `SUPABASE_SERVICE_ROLE_KEY` pada frontend; aplikasi terhubung ke PostgreSQL langsung dari backend dan tidak memerlukan Supabase client key.
5. Setelah restore diverifikasi, deploy ulang API dan bot dengan database Supabase yang sama. Pastikan keduanya berjalan dengan `DATABASE_URL` baru sebelum mengaktifkan kembali penulisan. Periksa login akun lama dan state keuangan lewat dashboard/bot. Biarkan database Railway yang lama utuh sebagai rollback sampai pemeriksaan selesai. Jika ada masalah, hentikan penulisan, kembalikan `DATABASE_URL` kedua service ke database Railway, lalu deploy ulang keduanya.
6. Buat domain publik hanya untuk service API. Salin domain tersebut ke `API_BASE_URL` pada `frontend/dashboard.html` (tanpa garis miring di akhir), dan pastikan `WEBAPP_URL` sama dengan origin Firebase agar CORS mengizinkan dashboard. Jika `API_BASE_URL` berubah, deploy ulang frontend:

```bash
firebase deploy --only hosting
```

Firebase Hosting menyediakan `/dashboard` dari `frontend/dashboard.html`; request `/api/*` dikirim langsung ke API Railway. Untuk pengembangan lokal, gunakan URL Supabase yang dapat diakses dari komputer Anda, bukan hostname internal `.railway.internal`.

Gunakan `MODE=webhook` hanya jika Anda memang mengatur webhook. Dalam mode itu, `WEBHOOK_URL` harus menunjuk ke domain publik service bot yang menjalankan `bot.main`, bukan otomatis domain API.

Mini App memuat `Telegram.WebApp`, memanggil `ready()`/`expand()`, dan meneruskan `initData` ke API. Backend memvalidasi tanda tangan HMAC-SHA256 menggunakan `BOT_TOKEN` dan menolak data kedaluwarsa sebelum mengaitkan akun. Jangan pernah menerima `telegram_id` dari body sebagai identitas.

## Data Pasar dan Batas

- Top/cek crypto, watchlist, alert, dan valuasi portofolio crypto menggunakan CoinGecko; tanpa API key, kuota endpoint publik berlaku. Atur `COINGECKO_API_KEY` bila memakai paket CoinGecko yang sesuai.
- Menu Aset menampilkan lima saham IDX pilihan dengan kenaikan harian tertinggi dari daftar pantauan 30 saham, serta futures emas, perak, minyak WTI, dan gas alam dari Yahoo Finance. Peringkat adalah pergerakan sesi terakhir, bukan pemindaian semua saham IHSG. Sumber ini tidak resmi, dapat tertunda, berubah, atau membatasi akses; harga futures dalam USD bukan harga eceran komoditas lokal. Harga pasar saham/komoditas tidak otomatis mengubah valuasi aset yang dicatat pengguna.
- Kurs memakai Frankfurter dengan data referensi ECB. Fear & Greed memakai alternative.me. Pesan pasar menyertakan disclaimer non-saran investasi.
- Data indeks IHSG serta harga emas lokal per gram belum disediakan.
- Webhook production memerlukan reverse proxy HTTPS yang merutekan path bot terpisah dari FastAPI. Belum ada frontend untuk pengelolaan command lanjutan/portofolio saham.

## Tes

```bash
pytest -q
```

Tes mencakup parser nominal Bahasa Indonesia, pembentukan menu, validasi Mini App, laporan/skor, autentikasi API, impor tanpa seed, isolasi Telegram, revisi state, dan mutasi bot ke penyimpanan bersama.
