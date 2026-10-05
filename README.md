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

Akun baru mendapat uji coba Pro selama 7 hari sejak status Pro pertama kali diperiksa. Semua fitur dashboard memerlukan masa trial atau langganan aktif; API juga menolak akses data setelah masa tersebut berakhir. Paket saat ini Rp20.000 untuk 1 bulan, Rp100.000 untuk 6 bulan, dan Rp180.000 untuk 1 tahun. Paket 6 bulan menghemat Rp20.000 dan paket tahunan menghemat Rp60.000 dibanding harga bulanan.

Pembayaran saat ini diverifikasi manual. Isi `PRO_PAYMENT_METHOD`, `PRO_PAYMENT_ACCOUNT`, dan `PRO_PAYMENT_ACCOUNT_NAME` untuk menampilkan instruksi transfer. Tetapkan `PRO_ADMIN_SECRET` dengan nilai rahasia yang kuat dan berbeda dari `APP_SECRET`. Order tertunda dapat dilihat melalui `GET /api/admin/pro/orders`; setelah pembayaran diverifikasi, aktifkan lewat `POST /api/admin/pro/orders/{order_id}/activate` dengan header `Authorization: Bearer $PRO_ADMIN_SECRET`. Jangan kirim secret admin ke browser atau commit `.env`.

Data entitlement dan order disimpan pada tabel `pro_access` dan `pro_orders`, yang dibuat otomatis saat API mulai. Row Level Security diaktifkan pada kedua tabel saat menggunakan PostgreSQL/Supabase; akses aplikasi tetap lewat backend.

## Menu dan Data

Menu utama Telegram menyediakan Web App dan 22 tombol fitur. Command keuangan mencakup `/catat`, `/riwayat`, `/saldo`, `/wallet`, `/transfer`, `/budget`, `/laporan`, `/grafik`, `/pengingat`, `/simulasi`, `/zona`, `/hubungkan`, dan `/hapusdata`. Command pasar mencakup `/topcrypto`, `/crypto`, `/indeks`, `/saham`, `/kurs`, `/watchlist`, `/alert`, `/portofolio`, `/emas`, dan `/feargreed`. `/alert BTC > 70000` dapat langsung membuat alert. Pengaitan memakai kode satu kali yang kedaluwarsa dalam 10 menit. Bot yang belum terhubung memakai akun lokal yang terisolasi per `telegram_id`; saat ditautkan, bot dan dashboard membaca state yang sama.

API menyimpan JSON state dashboard dengan revisi optimistis dalam database yang dikonfigurasi. Bot dan API menggunakan transaksi repository yang sama. `DATABASE_URL` menerima URL PostgreSQL Railway (`postgresql://...`) maupun SQLAlchemy (`postgresql+psycopg://...`); URL PostgreSQL standar otomatis menggunakan driver `psycopg` yang disertakan.

## Docker dan Produksi

```bash
docker compose up --build -d
```

Compose menjalankan API pada port 8000 dan bot pada polling dengan volume database persisten. Taruh reverse proxy HTTPS di depannya; jangan mengekspos database atau endpoint internal. Cadangkan volume `doctor-money-data` secara rutin. Untuk deployment Railway atau multi-instance, gunakan PostgreSQL.

### Deployment Railway Postgres + Firebase

Aplikasi ini adalah backend/bot Python dengan frontend HTML statis. Railway menjalankan API, bot, dan Postgres; Firebase Hosting tetap menyajikan halaman web. Kedua service aplikasi Railway harus memakai database Postgres yang sama.

1. Tambahkan service PostgreSQL pada project dan environment Railway yang sama dengan aplikasi. Di setiap service aplikasi, buat variable reference `DATABASE_URL` yang menunjuk ke `DATABASE_URL` milik service PostgreSQL (misalnya `${{Postgres.DATABASE_URL}}`, dengan nama service yang sesuai). Gunakan URL private internal Railway, bukan public proxy URL, untuk service yang berjalan di Railway.
2. Buat dua service Railway dari repository ini: satu untuk API dan satu untuk bot. Keduanya memakai konfigurasi `railway.json` dan script `start.sh`. URL PostgreSQL standar Railway (`postgresql://...`) otomatis diarahkan ke driver `psycopg`.
3. Pada service API, atur `APP_RUNTIME=api`. Pada service bot, atur `APP_RUNTIME=bot` dan `MODE=polling`. Buat domain publik hanya untuk service API; bot polling tidak memerlukan domain publik.
4. Isi variabel yang diperlukan pada kedua service: `DATABASE_URL`, `BOT_TOKEN`, `APP_SECRET`, `WEBAPP_URL=https://doctor-moneys.web.app`, dan `TZ=Asia/Jakarta`. `APP_SECRET` harus berupa nilai acak yang kuat dan sama pada kedua service. Tambahkan variabel `PRO_*` dan `COINGECKO_API_KEY` bila fitur tersebut digunakan. Untuk pengembangan lokal, pakai connection URL publik Railway atau tunnel; hostname `.railway.internal` hanya dapat diakses dari jaringan Railway.
5. Salin domain publik API Railway ke `API_BASE_URL` pada `frontend/dashboard.html` (tanpa garis miring di akhir). Pastikan `WEBAPP_URL` pada service API sama dengan origin Firebase Hosting agar CORS mengizinkan dashboard.
6. Deploy frontend ke Firebase Hosting:

```bash
firebase deploy --only hosting
```

Firebase Hosting menyediakan `/dashboard` dari `frontend/dashboard.html`; dashboard mengirim permintaan `/api/*` langsung ke API Railway. API dan bot berbagi akun, state, serta data lewat Railway Postgres. Jika domain API atau Firebase berubah, perbarui `API_BASE_URL`/`WEBAPP_URL` lalu deploy ulang frontend bila `API_BASE_URL` berubah. Jika lebih nyaman memakai Supabase, set `DATABASE_URL` ke URL Postgres Supabase dengan format `postgresql+psycopg://...` dan `sslmode=require` seperti yang ditunjukkan di `.env.example`.

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
