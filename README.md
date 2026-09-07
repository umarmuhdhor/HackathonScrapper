# Hackathon Screening

Scraper + dashboard pribadi untuk hackathon yang sedang berjalan. Sumber saat ini:

| Sumber | Cara ambil data |
|---|---|
| [Devpost](https://devpost.com/hackathons) | JSON feed publik `devpost.com/api/hackathons` (paginasi 9/halaman) |
| [lablab.ai](https://lablab.ai/ai-hackathons) | payload RSC Next.js (`self.__next_f`) di HTML — tanpa headless browser |
| [MLH](https://mlh.io/seasons/2027/events) | payload Inertia.js di `<script data-page="app">` — jadwal satu musim penuh |

Tiga bagian:

1. **Scraper** → normalisasi ke satu skema → SQLite (`data/hackathons.db`).
2. **API** → FastAPI di atas SQLite.
3. **Dashboard** → sidebar 6 halaman: Ringkasan, Papan Saya, Agenda, Rekomendasi, Jelajahi,
   Scraper.

## Setup

```bash
uv venv --python 3.13
uv pip install httpx fastapi "uvicorn[standard]"
```

## Pakai

```bash
./run.sh
```

Buka http://localhost:8765. Scrape pertama jalan otomatis kalau DB masih kosong.

## Data disimpan, bukan di-scrape ulang

Semua hasil scraping masuk ke `data/hackathons.db` dan **itulah yang dibaca dashboard**. Membuka
dashboard, memfilter, atau mencari tidak pernah menyentuh jaringan — hanya query SQLite.

Scraping ulang hanya terjadi kalau kamu memintanya, dan itupun dijaga cache TTL: sumber yang
sudah diambil dalam **6 jam terakhir** akan dilewati dengan status `cached`. Pakai `--force`
(CLI) atau centang *"paksa ambil ulang"* (halaman Scraper) untuk menerobosnya.

Baris lama tidak pernah dihapus — di-upsert. Jadi lomba yang sudah selesai tetap tersimpan
sebagai arsip, dan `first_seen_at` mencatat kapan pertama kali muncul.

## Scraping berfilter

Lewat CLI:

```bash
.venv/bin/python -m scraper.run --source devpost --devpost-status open --pages 3 --force
```

| Flag | Arti |
|---|---|
| `--source devpost\|lablab\|mlh\|all` | sumber yang dijalankan |
| `--devpost-status open upcoming ended` | open state Devpost yang diambil |
| `--mlh-season 2026 2027` | musim MLH (default: musim aktif, dihitung dari tanggal) |
| `--pages N` | maksimal halaman Devpost |
| `--ttl MENIT` | berapa lama data dianggap segar (0 = selalu ambil) |
| `--force` | abaikan cache |
| `--no-rules` | jangan otomatis baca halaman aturan Devpost setelah scraping |
| `--rules-budget N` | maksimal halaman aturan yang dibaca per run (default 40) |

Lewat dashboard: halaman **Scraper** punya semua filter di atas dalam bentuk form, plus kartu
status penyimpanan per sumber dan tabel riwayat scraping (termasuk error).

## Digest harian — top 5 minggu ini

```bash
./install-daily.sh          # jam 08:00 tiap hari
./install-daily.sh 7 30     # atau jam 07:30
```

Memakai **launchd**, bukan cron. Bedanya nyata di laptop: kalau Mac tidur saat jadwal tiba,
cron melewatkan pekerjaan itu diam-diam, sedangkan launchd menjalankannya saat bangun.

Tiap pagi `run_daily.sh` melakukan tiga hal berurutan:

1. Scrape ketiga sumber (`ttl_minutes=0`, jadi selalu ambil segar)
2. Baca halaman aturan yang belum diperiksa
3. Susun digest dan kirim notifikasi macOS berisi peringkat #1

Hasilnya ditulis ke `data/digests/`:

| File | Isi |
|---|---|
| `YYYY-MM-DD.md` | versi Markdown, enak dibaca di editor |
| `YYYY-MM-DD.html` | versi HTML, buka di browser |
| `YYYY-MM-DD.json` | data mentah, dipakai dashboard |
| `latest.html` | selalu menunjuk digest terbaru |
| `daily.log` | catatan tiap eksekusi |

Halaman **Digest harian** di dashboard menampilkan digest mana pun beserta arsipnya, lengkap
dengan tombol ♥/✕/+Lacak seperti di halaman Rekomendasi.

Manual, tanpa menunggu jadwal:

```bash
./run_daily.sh                                  # lengkap: scrape + aturan + digest
.venv/bin/python -m scraper.digest --skip-scrape --print   # susun ulang saja, cepat
```

Perintah pengelolaan:

```bash
launchctl list | grep hackathon-screening        # cek terpasang
launchctl start com.hackathon-screening.daily    # jalankan sekarang
launchctl unload ~/Library/LaunchAgents/com.hackathon-screening.daily.plist   # matikan
```

### Bagaimana top 5 dipilih

Kandidat harus lolos semuanya: berstatus berjalan/akan datang, belum ada di papanmu, tidak kamu
tandai ✕, dan **tidak berputusan ✕ tidak bisa** menurut profil kelayakanmu. Sisanya diperingkat
dengan skor yang sama seperti halaman Rekomendasi, dan yang kamu ♥ tetap disematkan di atas.

Tiap entri ditandai **baru** atau **lanjutan** dengan membandingkan digest sebelumnya, jadi kamu
langsung tahu mana yang belum pernah muncul — digest harian tidak berguna kalau isinya sama
persis tiap pagi.

Digest diberi tanggal menurut **waktu lokal mesinmu**, bukan UTC. Di WIB keduanya bisa beda
sehari kalau job jalan larut malam, dan nama file harus cocok dengan yang kamu sebut "hari ini".
Timestamp di dalamnya tetap UTC.

### Scraping berkala tanpa digest

Kalau hanya ingin data segar tanpa digest:

```bash
0 */6 * * * cd /path/ke/Screening && .venv/bin/python -m scraper.run >> data/scrape.log 2>&1
```

## Halaman dashboard

**Ringkasan** — banner peringatan kalau ada deadline mendesak, kartu statistik (dilacak,
deadline ≤7 hari, prize pool aktif, sudah submit + win rate, sedang berjalan, total tersimpan),
deadline terdekat 30 hari, aktivitas terakhir, dan status kesegaran tiap sumber.

**Papan Saya** — kanban 6 kolom: Tertarik → Terdaftar → Dikerjakan → Submitted → Menang / Kalah.
Kartu menampilkan hitung mundur, progress bar, jumlah checklist, dan nama proyek. Warna garis
kiri = prioritas. Ada filter teks dan prioritas di atas papan.

**Agenda** — semua deadline yang belum kamu submit, urut paling dekat, dengan pilihan rentang
7 / 30 / 90 hari. Memakai target pribadimu kalau diisi, kalau tidak pakai deadline resmi.

**Rekomendasi** — lomba yang belum kamu lacak, diperingkat dari kecocokan dengan papanmu.
Lihat bagian [Rekomendasi](#rekomendasi) di bawah.

**Jelajahi** — seluruh isi database dengan filter:

| Filter | Pilihan |
|---|---|
| **Bisa ikut?** | **yang bisa saya ikuti / aman / perlu dicek / tidak bisa / semua** |
| Pencarian teks | judul, tema, penyelenggara, lokasi |
| Sumber | chip devpost / lablab / mlh, bisa pilih lebih dari satu |
| Status | berjalan, akan datang, gabungan, selesai, semua |
| **Peserta untuk** | **mahasiswa/pelajar atau umum** |
| Format | online / onsite / hybrid |
| Tema | 40 tema teratas beserta jumlahnya |
| Hadiah | ada hadiah, ≥$1.000, ≥$10.000, ≥$50.000 |
| Tutup dalam | ≤3 / ≤7 / ≤30 hari |
| Durasi | kilat ≤2 hari, ≤1 minggu, ≤1 bulan |
| Ukuran | ≥100 / ≥1.000 / ≥5.000 pendaftar |
| Baru masuk | muncul di database ≤7 hari terakhir |
| Urutan | deadline / hadiah / peserta / terbaru masuk / judul |
| Sembunyikan | yang sudah kamu lacak |

**Scraper** — form filter scraping, status penyimpanan, riwayat run.

Kotak pencarian di header berlaku global: mengetik di sana langsung melompat ke Jelajahi.
Tekan `/` untuk fokus ke kotak itu.

## Profil kelayakan — supaya tidak disodori lomba yang tak bisa kamu ikuti

Tombol **⚙ Profil kelayakan** di bawah sidebar menyimpan empat hal:

| Setelan | Dipakai untuk |
|---|---|
| Negara tempat tinggal (ISO-2) | membandingkan dengan lokasi lomba onsite |
| Status pendidikan | none / baru lulus-non gelar / SMA / S1 / S2-S3 |
| Kesediaan hadir langsung | online saja / dalam negeri / bisa ke luar negeri |
| Tampilkan lomba undangan | default mati |

Dialognya menunjukkan dampaknya langsung sebelum disimpan ("134 bisa ikut · 75 perlu dicek ·
0 tersaring"). Profil disimpan di tabel `settings`.

### Kasus "baru lulus / program non-gelar"

Pilihan **baru lulus / program non-gelar** (bootcamp, Apple Developer Academy, dan sejenisnya)
sengaja dibuat terpisah. Kamu bukan mahasiswa terdaftar, tapi juga bukan publik umum: MLH dan
banyak hackathon kampus menerima lulusan ≤12 bulan, sebagian lagi tidak. Karena itu lomba
bertanda mahasiswa **tidak digugurkan** untukmu — statusnya jadi ⚠ perlu dicek dengan catatan
alasannya, supaya kamu yang memutuskan setelah baca aturannya.

### Tiga putusan

| Putusan | Arti |
|---|---|
| ✓ **bisa ikut** | halaman aturannya sudah dibaca dan tidak ada yang melarangmu. Hanya mungkin untuk Devpost |
| ⚠ **perlu dicek** | ada yang tidak bisa dipastikan — aturannya belum/tidak bisa dibaca (semua lomba lablab.ai & MLH), venue kampus, atau lokasi onsite yang negaranya tidak terbaca |
| ✕ **tidak bisa** | ada sinyal keras yang menghalangi |

Alasannya selalu ditulis di bawah judul lomba, mis. *"Lokasinya di US, di luar ID"* atau
*"Digelar di kampus — sering diprioritaskan/khusus mahasiswa kampus itu"*.

### Sinyal keras yang menghalangi (✕)

1. **Negaramu ada di daftar larangan aturan resmi lomba** — dibaca dari halaman rules-nya
2. Aturannya mensyaratkan status mahasiswa aktif padahal kamu bukan
3. `invite_only` dari Devpost — **10 lomba** di database saat ini
4. Pendaftaran ditutup di lablab.ai (`signupActive=false`) — **53 lomba**
5. Onsite/hybrid padahal kamu memilih "online saja"
6. Onsite di negara lain padahal kamu memilih "dalam negeri saja"
7. Lomba bertanda mahasiswa padahal kamu bukan mahasiswa (kecuali status "baru lulus /
   non-gelar" — jadi ⚠ perlu dicek)
8. Lombanya sudah selesai

### Membaca aturan resmi lomba

Feed Devpost sama sekali tidak menyebut siapa yang boleh ikut. Halaman aturannya menyebut, dan
di situlah larangan yang paling menentukan berada. Hackathon bersponsor memakai boilerplate:

> The Hackathon IS NOT open to: Individuals who are residents of, or Organizations domiciled in,
> a country ... where the laws of the United States or local law prohibits participating or
> receiving a prize (including, but not limited to, Argentina, Australia, Brazil, Hong Kong,
> **Indonesia**, Italy, Malaysia, Philippines, Thailand, Vietnam, Singapore, ...)

`scraper/rules.py` mengambil `<event>/rules`, membersihkan HTML-nya, mencari bagian
"IS NOT open to" yang dibingkai sebagai batasan domisili, lalu memetakan nama negara ke kode
ISO-2. Hasilnya disimpan di kolom `excluded_countries`, `requires_student`,
`eligibility_snippet`, dan `rules_checked_at`. Kutipan aslinya bisa dibuka di panel detail lomba,
jadi keputusan mesin selalu bisa dicek ke sumbernya.

Dua jebakan yang sudah ditangani: frasa "the laws of the **United States**" dan "**United States**
Treasury's Office of Foreign Assets Control" bukan berarti Amerika dilarang, dan "(Province of)
**Quebec**", "Crimea", "Donetsk", "Luhansk" adalah batasan sub-nasional — tanpa penyaringan ini
hampir semua hackathon bersponsor akan tampak melarang seluruh AS dan Kanada.

#### Lomba baru dicek otomatis

Setiap scraping Devpost langsung membaca aturan lomba yang belum pernah dicek, maksimal 40 per
run (`--rules-budget`). Jadi lomba baru tidak pernah menganggur dengan kelayakan tak diketahui.
Matikan dengan `--no-rules` kalau sedang ingin cepat.

```
  devpost  found=36   new=2    updated=34
           aturan: 2 terbaca, 0 punya larangan negara, 0 gagal
```

Kalau jumlahnya melebihi budget, sisanya dilaporkan (`sisa N aturan belum dibaca`) dan halaman
Rekomendasi memunculkan spanduk peringatan — jumlah yang belum terbaca tidak pernah disembunyikan.

Pemeriksaan manual:

```bash
.venv/bin/python -m scraper.rules
```

Atau tombol **Periksa aturan yang belum dicek** di halaman Scraper. Satu permintaan HTTP per
lomba dengan jeda 0,4 detik, jadi pemeriksaan awal 133 lomba makan sekitar empat menit; hasilnya
tersimpan dan tidak diulang. Status terkini: **133/133 lomba Devpost yang masih hidup sudah
dicek — 6 punya larangan negara, 4 mensyaratkan mahasiswa aktif, 1 melarang ID.**

#### Tiga keadaan, bukan dua

Kolom `rules_ok` memisahkan "sudah dibaca dan bersih" dari "gagal dibaca" — tanpa itu, satu
permintaan HTTP yang gagal akan terlihat sama seperti lomba yang aturannya sudah diverifikasi.

| Keadaan | `rules_ok` | Putusan |
|---|---|---|
| Belum pernah dicek | `NULL` | ⚠ "Aturan detailnya belum diperiksa" |
| Halaman gagal diambil | `0` | ⚠ "Halaman aturannya gagal dibaca" |
| Terbaca, tidak ada larangan | `1` | ✓ bisa ikut |
| Terbaca, negaramu dilarang | `1` | ✕ tidak bisa, daftar negaranya ditulis |

Baris ber-`rules_ok = 0` dicoba lagi, tapi **paling cepat 7 hari kemudian**
(`RETRY_FAILED_AFTER_DAYS`). Tanpa jeda ini, 57 halaman yang memang tak terbaca akan digempur
ulang setiap kali job harian jalan — boros dan tidak sopan ke situs penyelenggara.

#### Cakupan per sumber

Ketiga sumber ikut diperiksa, tapi hasilnya jauh berbeda dan tabel di halaman Scraper
menampilkannya apa adanya:

| Sumber | Yang diambil | Kenyataannya |
|---|---|---|
| Devpost | `<event>/rules` | seragam, hampir selalu terbaca |
| MLH | situs acaranya (`websiteUrl`), lalu `/rules`, `/legal`, `/terms`, `/faq` | sebagian besar dirender JavaScript, jadi banyak yang tak terbaca |
| lablab.ai | halaman event + deskripsi yang sudah tersimpan | halaman event tidak memuat teks aturan sama sekali |

Sebuah halaman baru dihitung terbaca kalau **dua syarat** terpenuhi: teksnya lebih dari 1.200
karakter (di bawah itu cuma cangkang JavaScript) **dan** memuat penanda aturan seperti
"eligibility", "official rules", "terms and conditions", "not open to", atau "residents of".
Syarat kedua penting: tanpa itu, halaman pemasaran yang panjang akan tercatat sebagai "aturan
sudah dibaca, tidak ada larangan" — lampu hijau palsu, persis kesalahan yang ingin dihindari.

Yang tidak lolos syarat dicatat **tak terbaca** (`rules_ok = 0`), tetap ⚠, dan masuk antrean
percobaan berikutnya.

#### Klaim keterbukaan

Kalau deskripsi lomba menyebut sendiri "open to everyone", "from anywhere in the world", atau
sejenisnya, kalimatnya disimpan di `openness_claim` dan bisa dibuka di panel detail. Klaim ini
**tidak pernah menghasilkan ✓** — itu materi promosi penyelenggara, bukan halaman aturan. Yang
berubah hanya kalimat peringatannya, jadi kamu tahu ada indikasi positif tapi belum terverifikasi.

### Dari mana datanya

Hanya tiga sinyal kelayakan yang benar-benar terstruktur di ketiga sumber:

| Sinyal | Sumber | Cakupan |
|---|---|---|
| `invite_only` + catatannya | Devpost | selalu ada |
| `venueAddress.country` (ISO-2) | MLH | semua event onsite |
| `signupActive` | lablab.ai | selalu ada |

Sisanya diterka. Negara untuk lomba Devpost onsite dipulihkan dari teks lokasi yang berantakan
("Detroit, MI, USA" → US, "Bengaluru, India" → IN, "Waterloo, ON" → CA). Kalau tidak terbaca —
mis. "Carnegie Mellon University" atau "Distribution Hall" — negaranya **dibiarkan kosong** dan
lombanya jadi ⚠ perlu dicek, bukan dianggap aman. Saat ini 98 lomba punya negara, 62 onsite
lainnya tidak.

**Yang tidak bisa dijamin:** batas umur, syarat visa, kuota tim, dan aturan "khusus mahasiswa
kampus X" tetap tidak terbaca. Untuk MLH dan lablab.ai bahkan larangan negara pun tidak terbaca.
Penyaring ini membuang yang jelas-jelas tidak mungkin dan menandai yang meragukan — tetap baca
halaman aturan sebelum mendaftar.

## Rekomendasi

Tidak ada satu pun sumber yang menyediakan rating atau "event serupa", jadi peringkatnya adalah
**heuristik berbobot yang transparan** atas data yang memang tersimpan. Setiap kartu selalu
menyertakan alasannya, supaya bisa kamu bantah, bukan sekadar dipercaya.

Kandidat = lomba berstatus berjalan/akan datang, **belum ada di papanmu**, dan **lolos profil
kelayakanmu**. Yang berputusan ✕ tidak pernah direkomendasikan; jumlah yang dibuang ditulis di
atas daftar ("76 lomba disembunyikan karena tidak memenuhi syaratmu"). Pakai
`?include_ineligible=true` di API kalau memang mau melihatnya.

### Dua mode

**Berprofil** — begitu kamu melacak sesuatu, papanmu jadi profil: tema, segmen peserta, format,
sumber, dan kisaran hadiah yang biasa kamu pilih. Bobotnya:

| Sinyal | Bobot | Maksudnya |
|---|---|---|
| Kecocokan tema | 32 | irisan tema dengan tema yang sering kamu lacak |
| Segmen peserta | 14 | mahasiswa vs umum, sesuai kebiasaanmu |
| Waktu persiapan | 22 | ideal 5–45 hari; dipotong kalau mepet atau masih jauh |
| Hadiah | 12 | skala log, ditambah bonus kalau setara/di atas hadiah tengahmu |
| Format | 8 | online / onsite / hybrid |
| Jumlah pendaftar | 8 | skala log, indikasi lomba dikelola serius |
| Sumber | 4 | platform yang paling sering kamu pakai |

**Cold start** — papan masih kosong, jadi hanya sinyal netral yang dipakai: waktu persiapan (45),
hadiah (33), jumlah pendaftar (22). Kartunya diberi keterangan bahwa peringkat berasal dari
sinyal umum, bukan dari seleramu.

### Suka & tidak minat

Tiap kartu rekomendasi punya dua tombol:

| Tombol | Efek |
|---|---|
| **♥** | disematkan di urutan paling atas, apa pun skornya, sampai kamu batalkan |
| **✕** | tidak pernah muncul lagi di rekomendasi |

Klik tombol yang sama sekali lagi untuk membatalkan. Tombolnya juga ada di panel detail lomba,
jadi bisa diatur dari halaman mana pun. Jumlah yang tersembunyi selalu ditulis di atas daftar
("2 ditandai tidak diminati"), dan centang **tampilkan yang tidak diminati** memunculkannya
kembali supaya keputusanmu bisa ditinjau ulang.

Dua catatan jujur soal perilakunya:

- **♥ menembus penyaring kelayakan.** Kalau kamu menyukai lomba yang berputusan ✕ tidak bisa,
  lomba itu tetap tampil — itu pilihanmu sendiri, bukan rekomendasi saya — tapi label ✕ dan
  alasannya tetap ikut terpasang supaya tidak ada yang disembunyikan.
- **♥ dan ✕ tidak mengubah skor.** Keduanya hanya menyematkan dan menyembunyikan. Selera tetap
  dipelajari dari lomba yang kamu *lacak* di papan, bukan dari tombol ini.

Tersimpan di tabel `feedback`, terpisah dari `tracked` — menyukai sesuatu tidak sama dengan
mengikutinya.

### Filter

Halaman Rekomendasi punya pengarah sendiri: segmen peserta, format, chip sumber, dan
"tutup ≤14/30/60 hari" — berguna kalau kamu cuma mau lihat yang bisa dikejar bulan ini.
Panel di atas daftar menampilkan profil yang sedang dipakai (jumlah sampel, tema favorit,
segmen, format, hadiah tengah).

Tiga rekomendasi teratas juga muncul di halaman Ringkasan.

### Batasan

Skor **bukan prediksi menang** dan bukan penilaian kualitas lomba — hanya urutan kecocokan
dengan pola yang terlihat di papanmu. Dengan sampel di bawah ~5 lomba, profilnya masih goyah.
Tombol ✕ menyembunyikan kandidat yang tidak kamu minati, tapi itu tindakan manual — mesinnya
tidak belajar dari penolakanmu, hanya dari lomba yang kamu lacak.

## Notifikasi deadline

Lonceng di header memantau semua lomba yang kamu lacak dan **belum ditandai submit**. Angka merah
di lonceng = jumlah yang mendesak. Tingkatannya dihitung dari sisa waktu ke deadline efektif
(target pribadimu kalau diisi, kalau tidak deadline resmi):

| Tingkat | Sisa waktu | Tampilan |
|---|---|---|
| ⛔ terlewat | sudah lewat | merah, masuk banner |
| 🔴 kritis | ≤24 jam | merah, masuk banner, memicu popup desktop |
| 🟠 peringatan | ≤3 hari | oranye, masuk banner |
| 🟡 segera | ≤7 hari | daftar lonceng |
| ⚪ info | ≤14 hari | daftar lonceng |

Halaman Ringkasan menampilkan banner untuk tingkat kritis/terlewat (oranye kalau hanya
peringatan). Klik notifikasi mana pun untuk langsung membuka panel tracking-nya.

**Popup desktop** opsional: tombol *"aktifkan popup"* muncul di dropdown lonceng kalau browser
belum ditanya izin. Popup hanya untuk tingkat kritis/terlewat, dan maksimal sekali per lomba per
hari (dicatat di `localStorage`). Kalau browser memblokir izin, dropdown akan menyebutkannya —
notifikasi dalam aplikasi tetap jalan.

## Fitur tracking

Klik kartu atau tombol **+ Lacak** untuk membuka panel detail. Yang bisa dicatat:

- **Status** — 6 tahap, menentukan kolom di papan
- **Prioritas** 1–3 — tampil sebagai warna garis kiri kartu
- **Target pribadi** — deadline versimu sendiri, dipakai agenda dan hitung mundur menggantikan
  deadline resmi
- **Progress** 0–100% — slider, tampil sebagai progress bar
- **Checklist** — langkah-langkah dengan centang, terhitung di kartu (`☑ 2/5`)
- **Nama proyek, tim, link submission, catatan**
- **Riwayat** — timeline otomatis (mulai dilacak, perubahan status, perubahan progress) plus
  catatan manual yang bisa kamu tambah sendiri

Data tracking hidup di tabel `tracked` + `track_events` yang **terpisah dari hasil scraping**,
jadi scraping ulang tidak pernah menimpanya.

## Struktur

```
scraper/
  models.py        # dataclass Hackathon + parser tanggal/hadiah
  db.py            # skema SQLite, migrasi, upsert, cache TTL, event log
  run.py           # ScrapeOptions + orkestrasi, dipakai CLI dan API
  recommend.py     # profil dari papan + skoring rekomendasi
  eligibility.py   # profil pengguna + putusan bisa/tidaknya ikut
  rules.py         # baca halaman aturan: larangan negara & syarat mahasiswa
  digest.py        # digest harian: top N + render Markdown/HTML
  sources/
    devpost.py     # adapter Devpost
    lablab.py      # adapter lablab.ai
    mlh.py         # adapter Major League Hacking
api/main.py        # FastAPI
web/
  index.html       # shell dashboard
  app.css          # tema + layout sidebar
  app.js           # routing, filter, tracking (vanilla, tanpa build step)
data/hackathons.db # SQLite
```

## API

| Method | Path | Keterangan |
|---|---|---|
| GET | `/api/hackathons` | filter: `source`, `status` (koma = OR), `q`, `mode`, `theme`, `audience`, `eligibility` (`joinable`/`eligible`/`check`/`blocked`), `tracked`, `min_prize`, `has_prize`, `min_participants`, `max_days`, `ends_within_days`, `new_within_days`, `sort`, `limit`. Tiap item membawa objek `eligibility` |
| GET | `/api/hackathons/{id}` | satu lomba + riwayat tracking |
| GET | `/api/facets` | tema, format, sumber untuk isian filter |
| GET | `/api/tracked` | isi papan |
| PUT | `/api/tracked/{id}` | tambah/ubah entri papan (kirim objek utuh) |
| DELETE | `/api/tracked/{id}` | hapus dari papan |
| POST | `/api/tracked/{id}/events` | tambah catatan ke timeline |
| GET | `/api/feedback` | daftar yang disukai & ditolak |
| PUT | `/api/feedback/{id}` | body `{"kind": "like"\|"dismiss"}` |
| DELETE | `/api/feedback/{id}` | batalkan tanda |
| GET | `/api/profile` | profil kelayakan + dampaknya ke lomba yang masih hidup |
| PUT | `/api/profile` | ubah profil (`country`, `student_level`, `travel`, `allow_invite_only`) |
| GET | `/api/rules/status` | berapa lomba yang aturannya sudah dibaca, berapa yang melarang negaramu |
| POST | `/api/rules/check` | baca halaman aturan yang belum dicek (`limit`, `force`, `sources`) |
| GET | `/api/digest` | digest terbaru, atau `?date=YYYY-MM-DD` |
| GET | `/api/digests` | daftar tanggal digest yang tersedia |
| POST | `/api/digest/build` | susun ulang digest dari data tersimpan (tanpa scraping) |
| GET | `/api/recommendations` | peringkat kecocokan; filter `limit`, `audience`, `mode`, `source`, `max_days`, `include_tracked`, `include_ineligible`, `include_dismissed` |
| GET | `/api/notifications?horizon_days=14` | peringatan deadline berikut tingkatnya |
| GET | `/api/agenda?days=30` | deadline yang belum di-submit |
| GET | `/api/activity` | aktivitas tracking terakhir |
| GET | `/api/stats` | ringkasan angka |
| GET | `/api/sources` | jumlah tersimpan + kesegaran per sumber |
| GET | `/api/runs` | riwayat scraping |
| POST | `/api/scrape` | body: `sources`, `force`, `ttl_minutes`, `max_pages`, `devpost_statuses`, `mlh_seasons`, `auto_rules`, `rules_budget` |

`id` berformat `<sumber>:<id-asli>`, mis. `devpost:31230`,
`lablab:assemblyai-voice-agent-hackathon`, `mlh:hackrice-71`.

`PUT /api/tracked/{id}` mengganti seluruh isi entri, bukan menambal sebagian — kirim objek
lengkap (dashboard selalu begitu). Field yang tidak dikirim akan kembali ke nilai default.

## Catatan & batasan

- Devpost tidak memberi jam deadline di feed list, hanya tanggal — `end_at` diisi 23:59:59 UTC
  hari terakhir. Untuk jam persis, buka halaman lombanya.
- Hadiah lablab.ai diambil dari teks deskripsi (regex `$…`), jadi bisa kosong kalau penyelenggara
  tidak menulis nominal. Devpost punya field hadiah eksplisit.
- MLH tidak mempublikasikan nominal hadiah maupun jumlah peserta di feed musimnya, jadi kolom
  `prize_amount` dan `participants` kosong untuk sumber ini — filter "hadiah minimum" otomatis
  menyembunyikannya. Mayoritas event MLH onsite kampus (region AMER); `themes` diisi region plus
  tag fokus/underserved. Link mengarah ke situs hackathon-nya langsung, bukan halaman MLH.
- **Label "mahasiswa/umum" adalah tebakan, bukan aturan resmi.** Tidak satu pun dari ketiga
  sumber menyediakan field kelayakan peserta yang bisa dibaca mesin, jadi label diturunkan dari
  kata kunci di judul, penyelenggara, lokasi, dan tema (`university`, `college`, `campus`,
  `school`, `mahasiswa`, `siswa`, dst). MLH dianggap mahasiswa secara default karena memang liga
  antar-kampus, kecuali Global Hack Week yang terbuka untuk semua. Hasilnya sekarang: 113
  mahasiswa, 244 umum. Selalu cek halaman lombanya sebelum mendaftar — jangan andalkan label ini.
- Kolom `audience` diisi otomatis saat scraping; baris lama di-backfill sekali waktu database
  dibuka pertama kali setelah update.
- Ketiga sumber tidak butuh API key maupun login.

## Menambah sumber baru

Buat `scraper/sources/<nama>.py` dengan konstanta `NAME` dan fungsi
`fetch(**kwargs) -> list[Hackathon]`, lalu daftarkan di `scraper/sources/__init__.py`.
Kalau sumber itu punya filter khusus, tambahkan di `ScrapeOptions.kwargs_for()`.
Sisanya (DB, API, dashboard, chip filter) ikut otomatis.
