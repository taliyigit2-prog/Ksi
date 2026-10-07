# KSI Local Studio 2.0 — Kullanım ve Sınırlar

## Başlamadan önce

1. Uygulamayı bilgisayarın dahili diskindeki Applications klasöründen açın.
2. İlk kurulum dahili çalışma alanını oluşturur; harici disk bağlamanız gerekmez.
3. Alt bölümde `Hazır` mesajını bekleyin.
4. İlk kullanımda `Sistem Durumu` düğmesiyle araçları ve modelleri kontrol edin.

Arayüz dili üst bölümden Türkçe, Rusça, İngilizce, İspanyolca, Almanca, Fransızca, İtalyanca
veya Basitleştirilmiş Çince olarak değiştirilebilir. Seçim yeniden açılışta korunur ve kaynak
içeriğin diliyle bağımsızdır.

`Sistem Durumu` hızlı kontrolde model dosyalarının varlığını ve boyutlarını denetler. `Modelleri
Tam Doğrula` yaklaşık 12 GiB veriyi diskten okuyup SHA-256 karşılaştırması yapar. Bu işlem modeli
belleğe yüklemez ve yapay zekâ çalıştırmaz; yalnız gerektiğinde yapılmalıdır.

## Normal kullanım

Bir YouTube/X bağlantısı yapıştırın veya MP4, MKV, MOV, WEBM, SRT, VTT, PDF, DOCX, MD ya da TXT
dosyasını sürükleyin. Kaynak dilini bilmiyorsanız `Dili otomatik algıla` seçili kalsın. `Sadece indir`,
`Türkçe altyazı`, `Türkçe yazılı özet` ve `Türkçe dublaj` seçeneklerinden gerekenleri işaretleyin.

`İncele` yalnız başlık, süre, mevcut altyazı ve disk bütçesini denetler. İndirme, ön inceleme
penceresinde `Onayla ve Başlat` seçilmeden başlamaz. İş bittikten sonra çıktıyı inceleyebilir,
Mac'e doğrulanmış biçimde kopyalayabilir veya eksik çıktı türünü aynı işten ekleyebilirsiniz.

Belge türü otomatik seçilir. TXT dosyasını konuşma dökümü olarak işlemek istiyorsanız iş türünü
elle `Video veya konuşma metni` yapın. Belge ön incelemesinde onay verdiğinizde güvenli SSD içe
aktarması ve metin çıkarma art arda çalışır. Belge için `Türkçe altyazı` seçiliyse metin
çıkarıldıktan sonra bloklar otomatik olarak Türkçeye çevrilir. Geçmişte `Türkçeye çevrildi`
görüldüğünde `Belge Çevirisini İncele` düğmesi kaynak ve Türkçe blokları yan yana gösterir. İşe
özel terimleriniz varsa başlatmadan önce `Belge sözlüğü` alanından KSI Local Studio JSON sözlüğü
seçebilirsiniz.

`Türkçe yazılı özet` seçildiğinde `Kısa`, `Standart` veya `Ayrıntılı` uzunluk seçilebilir. Özet
kaynağını `Otomatik` bırakırsanız hazır ve kalite kontrolünden geçmiş Türkçe çeviri tercih edilir;
çeviri yoksa kaynak belge kullanılır. İsterseniz bu seçimi elle kaynak belgeye veya doğrulanmış
Türkçe çeviriye sabitleyebilirsiniz. `Okunaklı PDF özeti de oluştur` seçeneği MD ve DOCX'e ek olarak
PDF üretir.

Belge çevirisi tamamlandığında TXT, Markdown, DOCX ve gömülü fontlu PDF hazırlanır. `Mac çıktısı`
alanından Masaüstü, Belgeler veya `Klasör Seç` ile başka bir dahili klasör seçebilirsiniz.
`Mac'e Kopyala` öncesinde hedefte çıktı boyutuna ek 2 GiB boş alan aranır. Dosyalar gizli klasöre
kopyalanır, kaynak ve kopya SHA-256 değerleri doğrulanır ve ardından görünür olur. Aynı isim varsa
mevcut klasör değişmez; yeni kopya `(2)`, `(3)` adıyla oluşturulur ve Finder'da gösterilir.

## Desteklenenler

- Apple Silicon Mac ve macOS 14 veya üzeri
- En fazla üç saatlik tek video ya da tek ders
- YouTube, herkese açık X, deneysel tek Udemy dersi ve yerel dosya
- İngilizce, Rusça, İspanyolca, Almanca, Çince, Fransızca ve İtalyancadan Türkçeye çeviri
- Otomatik kaynak dil algılama
- PDF, DOCX, Markdown ve UTF-8 TXT belgelerinden yerel kanonik metin çıkarma
- Taranmış PDF sayfalarında macOS Apple Vision ile çevrimdışı OCR
- Aynı belgedeki farklı dilleri blok bazında algılayarak yedi kararlı dilden Türkçeye çeviri
- Sayı, URL, e-posta, kod, denklem belirteci, dipnot ve özel ad koruması
- Belgeye özel terim sözlüğü, kesinti sonrası checkpoint ve kaynak–Türkçe inceleme ekranı
- Belge bloklarına ve gerçek sayfa/paragraf/satır konumlarına bağlı Türkçe özet
- Kısa, standart ve ayrıntılı özet profilleri; Markdown, DOCX ve isteğe bağlı PDF çıktı
- Türkçe belge çevirisi için TXT, yapıyı koruyan Markdown, düzenli DOCX ve gömülü fontlu PDF
- Masaüstü, Belgeler veya seçilen dahili klasöre doğrulanmış ve çakışmasız kopyalama
- Türkçe SRT, zaman kodlu Markdown özet ve düşük tonlu erkek sesli dublaj
- İş kesilmesi veya disk erişim hatasından sonra güvenli yeniden deneme

## Bilinen sınırlar

- Oynatma listesi, bütün kanal veya bütün Udemy kursu toplu olarak indirilmez.
- Canlı yayın işlenmez; 4K yerine en fazla 1080p seçilir.
- DRM, Widevine, CAPTCHA, ücret duvarı veya erişim denetimi aşılmaz.
- Udemy desteği yalnız erişim hakkınız olan, DRM'siz tek ders için deneyseldir.
- X ve Udemy oturumu gerekiyorsa yalnız bu amaçla oluşturulmuş ayrı tarayıcı profili kullanılır.
- Ses klonlama, dudak senkronu ve konuşmacıya göre farklı ses yoktur.
- Dublaj M2/16 GB cihazda gerçek zamandan belirgin biçimde yavaş olabilir.
- Yerel küçük modeller düzgün fakat kusursuz olmayan Türkçe üretebilir. Altyazı, özet ve özellikle
  teknik terimler insan tarafından kontrol edilmelidir. Gerekirse Codex inceleme paketi kullanılır.
- Üç saatlik kabul; zaman kodu, SRT, özet parçalaması ve disk bütçesini tam sınırda sınamıştır.
  Üç saatlik gerçek model dublajı saatler süreceği için otomatik son kabul sırasında çalıştırılmamıştır.
- Çok sütunlu veya sıra dışı PDF düzenlerinde okuma sırası insan kontrolü gerektirebilir.
- Düşük güvenli OCR satırları kalite raporunda işaretlenir; otomatik rapor metnin doğruluğunu garanti
  etmez.
- Karmaşık DOCX metin kutuları, WordArt, denklemler ve değişiklik izleme eksiksiz korunmayabilir;
  bulunan riskler kalite raporunda gösterilir.
- Portekizce, Japonca, Korece, Arapça, Hollandaca, Lehçe, Ukraynaca, Çekçe, İsveççe ve Yunanca
  algılanabilir fakat temiz, teknik ve uzun paragraf pilotlarının tamamı geçmeden çeviri menüsüne
  açılmaz. Böyle bir blok görülürse program tahminde bulunarak yanlış çeviri üretmek yerine durur.
- Kaynak blok ve birebir kanıt denetimi, özetin anlam doğruluğunu tek başına garanti etmez; final
  özet toplu manuel kabulte kaynakla karşılaştırılmalıdır.

## Depolama ve temizlik

Modeller, kaynak video ve sonuçlar bilgisayarın dahili diskinde tutulur. Çalışma alanı
`Library/Application Support/KSI Local Studio/KSI-Workspace` altındadır. Eski harici disk
ayarları ve dosyaları korunur; yeni kurulum eski diskin bağlanmasını gerektirmez.
Dahili diskte en az 20 GiB güvenlik payı korunur. Önceki kabulde üç saatlik 1080p üst sınır
senaryosu modeller önceden kuruluyken yaklaşık 49,0 GiB çalışma + güvenlik alanı gerektirdi.

`Disk Ara Dosyalarını Temizle` yalnız seçili ve tamamlanmış işin yeniden üretilebilir dublaj
segmentlerini, geçici PCM sesini ve yarım checkpoint dosyalarını listeler. Kaynak video, kaynak
transkript, Türkçe altyazı, özet ve final dublaj otomatik silinmez. Liste gösterilmeden ve siz açıkça
onaylamadan temizlik yapılmaz.

Mac'e kopyalama diskteki kaynağı silmez. Dosya boyutu ve SHA-256 doğrulanmadan görünür hedef
oluşturulmaz; hedefte 2 GiB güvenlik payı bırakılır. Masaüstü iCloud ile eşitleniyorsa büyük videolar
Apple hesabınıza yüklenebilir.

## İş geçmişi

Geçmişin üstündeki arama alanı iş kimliği, güvenli kaynak gösterimi, tür, dil, çıktı ve durum
üzerinde arama yapar. Filtre menüsü video, belge, tamamlanan ve ilgi gerektiren işleri ayırır.
Seçili satırda sağ tıklayarak bağlantıyı veya yerel dosya yolunu kopyalayabilir, kaynağı açabilir,
iş kimliğini kopyalayabilir, çıktı klasörünü, final videoyu ya da son dışa aktarma klasörünü
açabilirsiniz. Seçili hücrede standart `Cmd+C` kısayolu da kullanılabilir.

Geçmiş ve pano için URL kullanıcı adı/parolası, parça bilgisi ve hassas sorgu parametreleri
atılır. Örneğin video kimliği gibi güvenli ve gerekli bir sorgu değeri korunabilir; çerez, parola
ve erişim tokenı hiçbir zaman araç ipucuna, panoya veya kalıcı günlüğe yazılmaz.

## Yapay zekâ ve mahremiyet

KSI Local Studio ücretli API kullanmaz ve dosyaları otomatik olarak buluta göndermez. Whisper, Ollama ve
Chatterbox yalnız gereken aşamada çalışır. Ollama modelleri aşama sonunda `keep_alive: 0` ile
bellekten çıkarılır; uygulamanın başlattığı özel sunucu kapatılır. Kullanıcının önceden çalıştırdığı
Ollama sunucusu kapatılmaz. `ollama ps` çıktısının boş olması bellekte model olmadığını gösterir.

Parola uygulamaya girilmez. Tarayıcı profili yolu, çerez ve URL sorguları iş geçmişine veya
kalıcı loglara yazılmaz. Codex paketi video/ses içermez; yalnız doğrulanmış metin ve manifest içerir.

## Sorun olduğunda

- Disk erişim hatasında boş alanı ve çalışma klasörünün erişim izinlerini kontrol edin.
- Araç/model hatasında `Sistem Durumu` ekranını açın; eksik görünen bileşenin adını not edin.
- Yarım iş için geçmişten işi seçip `Seçili İşi Yeniden Dene` düğmesini kullanın.
- Udemy/X oturum hatasında ana tarayıcı profilini değil ayrı profili yeniden seçin.
- Çeviri kalitesi yetersizse isteğe bağlı `İnceleme Paketi Oluştur` ile metni dışarı alın.
- Paket açılmazsa `docs/FAZ11_DURUM.md` raporundaki sürüm ve mimari değerleriyle karşılaştırın.
