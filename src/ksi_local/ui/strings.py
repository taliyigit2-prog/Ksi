"""Complete native navigation vocabulary for all existing interface locales."""

from ksi_local.i18n import SUPPORTED_UI_LANGUAGES


NAV_KEYS = ("download", "video", "document", "images", "queue", "history", "library", "help", "settings")
NAV = {
    "tr": ("İndir", "Video ve Ses", "Belge ve Web", "Görseller", "Kuyruk", "Geçmiş", "Kütüphane", "Yardım", "Ayarlar"),
    "en": ("Download", "Video and Audio", "Documents and Web", "Images", "Queue", "History", "Library", "Help", "Settings"),
    "ru": ("Скачать", "Видео и аудио", "Документы и веб", "Изображения", "Очередь", "История", "Библиотека", "Помощь", "Настройки"),
    "es": ("Descargar", "Vídeo y audio", "Documentos y web", "Imágenes", "Cola", "Historial", "Biblioteca", "Ayuda", "Ajustes"),
    "de": ("Herunterladen", "Video und Audio", "Dokumente und Web", "Bilder", "Warteschlange", "Verlauf", "Bibliothek", "Hilfe", "Einstellungen"),
    "fr": ("Télécharger", "Vidéo et audio", "Documents et web", "Images", "File d’attente", "Historique", "Bibliothèque", "Aide", "Réglages"),
    "it": ("Scarica", "Video e audio", "Documenti e web", "Immagini", "Coda", "Cronologia", "Libreria", "Aiuto", "Impostazioni"),
    "zh": ("下载", "视频和音频", "文档和网页", "图像", "队列", "历史记录", "资源库", "帮助", "设置"),
}

LANG_ORDER = SUPPORTED_UI_LANGUAGES
# Column order: tr, ru, en, es, de, fr, it, zh.
COPY = {
    "local": ("Tamamen yerel medya stüdyosu", "Локальная медиастудия", "Fully local media studio", "Estudio multimedia local", "Lokales Medienstudio", "Studio multimédia local", "Studio multimediale locale", "完全本地的媒体工作室"),
    "drop": ("Dosyayı buraya sürükle ve bırak", "Перетащите файл сюда", "Drop a file here", "Arrastra un archivo aquí", "Datei hier ablegen", "Déposez un fichier ici", "Trascina un file qui", "将文件拖放到此处"),
    "choose": ("Dosya seç", "Выбрать файл", "Choose file", "Elegir archivo", "Datei wählen", "Choisir un fichier", "Scegli file", "选择文件"),
    "single": ("Bu işlem için tek dosya seçin.", "Выберите один файл для этой операции.", "Choose one file for this workflow.", "Elige un archivo para este proceso.", "Eine Datei für diesen Ablauf wählen.", "Choisissez un fichier pour ce traitement.", "Scegli un file per questa operazione.", "此流程请选择一个文件。"),
    "source_hint": ("Bağlantı ekleyebilir veya yerel bir dosya seçebilirsin.", "Добавьте ссылку или выберите локальный файл.", "Add a link or choose a local file.", "Añade un enlace o elige un archivo local.", "Link hinzufügen oder lokale Datei wählen.", "Ajoutez un lien ou choisissez un fichier local.", "Aggiungi un collegamento o scegli un file locale.", "添加链接或选择本地文件。"),
    "download_hint": ("İndirmek istediğin bağlantıyı veya erişim hakkın olan içeriği ekle.", "Добавьте ссылку на доступный вам материал.", "Add a link to content you are authorized to download.", "Añade un enlace al contenido que puedes descargar.", "Link zu Inhalten hinzufügen, die du herunterladen darfst.", "Ajoutez un lien vers un contenu que vous pouvez télécharger.", "Aggiungi un link a contenuti che puoi scaricare.", "添加您有权下载的内容链接。"),
    "video_hint": ("Yazıya dök, çevir, altyazı ekle veya yerel dublaj üret.", "Расшифруйте, переведите, добавьте субтитры или озвучку.", "Transcribe, translate, subtitle or dub locally.", "Transcribe, traduce, subtitula o dobla localmente.", "Lokal transkribieren, übersetzen, untertiteln oder vertonen.", "Transcrivez, traduisez, sous-titrez ou doublez localement.", "Trascrivi, traduci, sottotitola o doppia localmente.", "在本地转录、翻译、添加字幕或配音。"),
    "document_hint": ("Belgelerini ve web yazılarını yerel olarak çevir veya özetle.", "Переводите и обобщайте документы и статьи локально.", "Translate or summarize documents and articles locally.", "Traduce o resume documentos y artículos localmente.", "Dokumente und Artikel lokal übersetzen oder zusammenfassen.", "Traduisez ou résumez documents et articles localement.", "Traduci o riassumi documenti e articoli localmente.", "在本地翻译或总结文档和文章。"),
    "media_tools": ("Dönüştür, sıkıştır veya kes", "Конвертировать, сжать или обрезать", "Convert, compress or trim", "Convertir, comprimir o cortar", "Konvertieren, komprimieren oder schneiden", "Convertir, compresser ou couper", "Converti, comprimi o taglia", "转换、压缩或剪切"),
    "translation": ("Çeviri ve dublaj", "Перевод и озвучка", "Translation and dubbing", "Traducción y doblaje", "Übersetzung und Vertonung", "Traduction et doublage", "Traduzione e doppiaggio", "翻译和配音"),
    "library_hint": ("Doğrulanmış kaynakları çevrimdışı ara. Bağlantı açmak senin seçiminle olur.", "Ищите проверенные источники офлайн. Ссылки открываются только по вашему выбору.", "Search verified resources offline. Links open only when you choose.", "Busca fuentes verificadas sin conexión. Los enlaces se abren si lo eliges.", "Geprüfte Quellen offline suchen. Links nur auf Wunsch öffnen.", "Recherchez des sources vérifiées hors ligne. Ouvrez les liens à votre choix.", "Cerca fonti verificate offline. I link si aprono solo su tua scelta.", "离线搜索已验证资源。仅在您选择时打开链接。"),
    "search": ("Ara…", "Поиск…", "Search…", "Buscar…", "Suchen…", "Rechercher…", "Cerca…", "搜索…"),
    "open_resource": ("Kaynağı aç", "Открыть источник", "Open resource", "Abrir recurso", "Quelle öffnen", "Ouvrir la source", "Apri risorsa", "打开资源"),
}


TOOL_COPY = {
    "preview": "Önizleme|Предпросмотр|Preview|Vista previa|Vorschau|Aperçu|Anteprima|预览",
    "preview_hint": "Küçük önizleme · desteklenen yerel görseller|Миниатюра · поддерживаемые локальные изображения|Thumbnail · supported local images|Miniatura · imágenes locales compatibles|Miniatur · unterstützte lokale Bilder|Miniature · images locales compatibles|Miniatura · immagini locali supportate|缩略图 · 支持的本地图像",
    "remove_background_video": "AI video arka planını kaldır|Удалить фон видео с ИИ|AI video background removal|Eliminar fondo de vídeo con IA|KI-Videohintergrund entfernen|Supprimer le fond vidéo par IA|Rimuovi sfondo video con IA|AI 移除视频背景",
    "video_mask_hint": "En fazla 30 sn · 20 fps · 1280×720. MOV: şeffaf; MP4: siyah arka plan.|До 30 с · 20 кадров/с · 1280×720. MOV: прозрачность; MP4: чёрный фон.|Up to 30 s · 20 fps · 1280×720. MOV: alpha; MP4: black background.|Hasta 30 s · 20 fps · 1280×720. MOV: transparencia; MP4: fondo negro.|Bis 30 s · 20 fps · 1280×720. MOV: transparent; MP4: schwarzer Hintergrund.|30 s max · 20 fps · 1280×720. MOV : transparence ; MP4 : fond noir.|Fino a 30 s · 20 fps · 1280×720. MOV: trasparenza; MP4: sfondo nero.|最多 30 秒 · 20 fps · 1280×720。MOV：透明；MP4：黑色背景。",
    "batch_result": "Tamamlanan: {ok} · Hatalı: {failed} · İptal: {cancelled}|Готово: {ok} · Ошибки: {failed} · Отменено: {cancelled}|Completed: {ok} · Failed: {failed} · Cancelled: {cancelled}|Completados: {ok} · Errores: {failed} · Cancelados: {cancelled}|Fertig: {ok} · Fehler: {failed} · Abgebrochen: {cancelled}|Terminés : {ok} · Échecs : {failed} · Annulés : {cancelled}|Completati: {ok} · Errori: {failed} · Annullati: {cancelled}|完成：{ok} · 失败：{failed} · 取消：{cancelled}",
    "storage": "Depolama|Хранилище|Storage|Almacenamiento|Speicher|Stockage|Archiviazione|存储",
    "open_workspace": "Çalışma alanını aç|Открыть рабочую папку|Open workspace|Abrir espacio de trabajo|Arbeitsordner öffnen|Ouvrir l’espace de travail|Apri area di lavoro|打开工作区",
    "tool_local": "Özgün dosyalar korunur; işlemler bu Mac’te yapılır.|Оригиналы сохраняются; обработка выполняется на этом Mac.|Originals are preserved; processing stays on this Mac.|Se conservan los originales; procesamiento en este Mac.|Originale bleiben erhalten; Verarbeitung auf diesem Mac.|Les originaux sont conservés; traitement sur ce Mac.|Gli originali sono conservati; elaborazione su questo Mac.|保留原文件；在此 Mac 上处理。",
    "operation": "İşlem|Операция|Operation|Operación|Vorgang|Opération|Operazione|操作",
    "format": "Biçim|Формат|Format|Formato|Format|Format|Formato|格式",
    "profile": "Profil|Профиль|Profile|Perfil|Profil|Profil|Profilo|配置",
    "start_time": "Başlangıç|Начало|Start|Inicio|Beginn|Début|Inizio|开始",
    "end_time": "Bitiş|Конец|End|Fin|Ende|Fin|Fine|结束",
    "end_file": "Dosya sonu|Конец файла|End of file|Fin del archivo|Dateiende|Fin du fichier|Fine del file|文件结尾",
    "trim_mode": "Kesme modu|Режим обрезки|Trim mode|Modo de corte|Schnittmodus|Mode de coupe|Modalità taglio|剪切模式",
    "subtitle": "Altyazı|Субтитры|Subtitle|Subtítulo|Untertitel|Sous-titre|Sottotitolo|字幕",
    "privacy": "Gizlilik|Конфиденциальность|Privacy|Privacidad|Datenschutz|Confidentialité|Privacy|隐私",
    "strip": "Metadata bilgilerini kaldır|Удалить метаданные|Remove metadata|Eliminar metadatos|Metadaten entfernen|Supprimer les métadonnées|Rimuovi metadati|移除元数据",
    "convert": "Dönüştür / sıkıştır|Конвертировать / сжать|Convert / compress|Convertir / comprimir|Konvertieren / komprimieren|Convertir / compresser|Converti / comprimi|转换／压缩",
    "convert_image": "Biçim dönüştür|Конвертировать формат|Convert format|Convertir formato|Format konvertieren|Convertir le format|Converti formato|转换格式",
    "trim": "Kes|Обрезать|Trim|Cortar|Schneiden|Couper|Taglia|剪切",
    "join": "Parçaları birleştir|Соединить фрагменты|Join clips|Unir clips|Clips verbinden|Joindre les clips|Unisci clip|合并片段",
    "remux": "Kayıpsız kap değiştir|Сменить контейнер без потерь|Lossless remux|Cambiar contenedor sin pérdida|Container verlustfrei wechseln|Changer de conteneur sans perte|Cambia contenitore senza perdita|无损更换容器",
    "burn_subtitle": "Kalıcı altyazı ekle|Встроить субтитры|Burn subtitles|Incrustar subtítulos|Untertitel einbrennen|Incruster les sous-titres|Incorpora sottotitoli|嵌入字幕",
    "optimize_png": "PNG kayıpsız optimize et|Оптимизировать PNG без потерь|Optimize PNG losslessly|Optimizar PNG sin pérdida|PNG verlustfrei optimieren|Optimiser PNG sans perte|Ottimizza PNG senza perdita|无损优化 PNG",
    "remove_background": "AI arka planı kaldır|Удалить фон с ИИ|AI background removal|Eliminar fondo con IA|KI-Hintergrund entfernen|Supprimer le fond par IA|Rimuovi sfondo con IA|AI 移除背景",
    "share": "Paylaşım · 1080p|Для публикации · 1080p|Share · 1080p|Compartir · 1080p|Teilen · 1080p|Partage · 1080p|Condivisione · 1080p|分享 · 1080p",
    "small": "Küçük dosya · 720p|Малый файл · 720p|Small file · 720p|Archivo pequeño · 720p|Kleine Datei · 720p|Petit fichier · 720p|File piccolo · 720p|小文件 · 720p",
    "archive": "Yüksek kalite|Высокое качество|High quality|Alta calidad|Hohe Qualität|Haute qualité|Alta qualità|高画质",
    "lossless": "Kayıpsız hızlı mod (anahtar kareye bağlı)|Быстрый режим без потерь (по ключевым кадрам)|Fast lossless mode (keyframe-bound)|Modo rápido sin pérdida (fotogramas clave)|Schnell und verlustfrei (Keyframes)|Rapide sans perte (images clés)|Rapido senza perdita (fotogrammi chiave)|快速无损模式（按关键帧）",
    "precision": "Kayıpsız kesme anahtar karelere bağlıdır. Hassas kesme yeniden kodlar.|Обрезка без потерь зависит от ключевых кадров. Точная обрезка перекодирует.|Lossless cuts follow keyframes. Precise cuts re-encode.|El corte sin pérdida sigue fotogramas clave. El preciso recodifica.|Verlustfreier Schnitt folgt Keyframes. Präziser Schnitt kodiert neu.|La coupe sans perte suit les images clés. La coupe précise réencode.|Il taglio senza perdita segue i fotogrammi chiave. Quello preciso ricodifica.|无损剪切按关键帧；精确剪切需重新编码。",
    "output_hint": "Yeni çıktı iş klasörüne kaydedilir; kaynak korunur.|Новый результат сохраняется в папке задачи; оригинал сохраняется.|New output is saved in the job folder; the source is preserved.|Resultado nuevo en la carpeta del trabajo; original conservado.|Neue Ausgabe im Auftragsordner; Quelle bleibt erhalten.|Nouveau résultat dans le dossier de tâche; source conservée.|Nuovo risultato nella cartella del lavoro; sorgente conservata.|新文件保存到任务文件夹；保留原文件。",
    "tool_start": "İşlemi başlat|Начать обработку|Start processing|Iniciar proceso|Verarbeitung starten|Lancer le traitement|Avvia elaborazione|开始处理",
    "stop": "Durdur|Остановить|Stop|Detener|Stoppen|Arrêter|Ferma|停止",
    "show_output": "Çıktıyı göster|Показать результат|Show output|Mostrar resultado|Ausgabe anzeigen|Afficher le résultat|Mostra risultato|显示输出",
    "completed": "Tamamlandı.|Готово.|Completed.|Completado.|Abgeschlossen.|Terminé.|Completato.|已完成。",
    "failed_start": "İşlem başlatılamadı|Не удалось запустить|Could not start|No se pudo iniciar|Start fehlgeschlagen|Impossible de démarrer|Avvio non riuscito|无法启动",
    "selected_files": "Seçilen dosyalar|Выбранные файлы|Selected files|Archivos seleccionados|Ausgewählte Dateien|Fichiers sélectionnés|File selezionati|已选文件",
}
COPY.update({key: tuple(value.split("|")) for key, value in TOOL_COPY.items()})


def text(key: str, locale: str) -> str:
    language = locale if locale in SUPPORTED_UI_LANGUAGES else "en"
    if key in NAV_KEYS:
        return NAV[language][NAV_KEYS.index(key)]
    return COPY[key][LANG_ORDER.index(language)]


def validate_catalog() -> None:
    if set(NAV) != set(SUPPORTED_UI_LANGUAGES) or any(len(values) != len(NAV_KEYS) for values in NAV.values()):
        raise ValueError("Eksik native gezinme çevirisi.")
    if any(len(values) != len(LANG_ORDER) for values in COPY.values()):
        raise ValueError("Eksik native ekran çevirisi.")
