import AppKit
import Foundation
import PDFKit
import Vision

struct CapabilityResult: Codable {
    let engine: String
    let revision: Int
    let supported_languages: [String]
    let automatic_language_detection: Bool
}

struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OCRResult: Codable {
    let engine: String
    let revision: Int
    let page_index: Int
    let pixel_width: Int
    let pixel_height: Int
    let lines: [OCRLine]
}

enum HelperError: Error, CustomStringConvertible {
    case usage(String)
    case failure(String)

    var description: String {
        switch self {
        case .usage(let message), .failure(let message):
            return message
        }
    }
}

func encode<T: Encodable>(_ value: T) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let data = try encoder.encode(value)
    guard let text = String(data: data, encoding: .utf8) else {
        throw HelperError.failure("OCR sonucu UTF-8 olarak kodlanamadı.")
    }
    print(text)
}

func capabilities() throws {
    let revision = VNRecognizeTextRequest.currentRevision
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    let languages = try request.supportedRecognitionLanguages().sorted()
    try encode(
        CapabilityResult(
            engine: "macOS Vision",
            revision: revision,
            supported_languages: languages,
            automatic_language_detection: true
        )
    )
}

func argument(_ name: String, in arguments: [String]) throws -> String {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        throw HelperError.usage("Eksik OCR argümanı: \(name)")
    }
    return arguments[index + 1]
}

func renderPage(_ page: PDFPage, maxDimension: Int) throws -> CGImage {
    let bounds = page.bounds(for: .mediaBox)
    guard bounds.width > 0, bounds.height > 0 else {
        throw HelperError.failure("PDF sayfa boyutu geçersiz.")
    }
    let largest = max(bounds.width, bounds.height)
    let scale = min(CGFloat(maxDimension) / largest, 4.0)
    let size = NSSize(
        width: max(1, floor(bounds.width * scale)),
        height: max(1, floor(bounds.height * scale))
    )
    let image = page.thumbnail(of: size, for: .mediaBox)
    var proposed = NSRect(origin: .zero, size: size)
    guard let cgImage = image.cgImage(forProposedRect: &proposed, context: nil, hints: nil) else {
        throw HelperError.failure("PDF sayfası yerel görüntüye dönüştürülemedi.")
    }
    return cgImage
}

func recognize(arguments: [String]) throws {
    let input = try argument("--input", in: arguments)
    guard let pageIndex = Int(try argument("--page-index", in: arguments)), pageIndex >= 0 else {
        throw HelperError.usage("OCR sayfa sırası sıfır veya daha büyük olmalıdır.")
    }
    guard let maxDimension = Int(try argument("--max-dimension", in: arguments)),
          (1000...4096).contains(maxDimension) else {
        throw HelperError.usage("OCR görüntü sınırı 1000-4096 piksel arasında olmalıdır.")
    }
    let url = URL(fileURLWithPath: input)
    guard url.isFileURL, let document = PDFDocument(url: url) else {
        throw HelperError.failure("PDF Apple PDFKit ile açılamadı.")
    }
    guard pageIndex < document.pageCount, let page = document.page(at: pageIndex) else {
        throw HelperError.failure("İstenen PDF sayfası bulunamadı.")
    }
    let cgImage = try renderPage(page, maxDimension: maxDimension)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.automaticallyDetectsLanguage = true
    let handler = VNImageRequestHandler(cgImage: cgImage, orientation: .up, options: [:])
    try handler.perform([request])
    let observations = (request.results ?? []).sorted { left, right in
        let verticalDifference = abs(left.boundingBox.midY - right.boundingBox.midY)
        if verticalDifference > 0.01 {
            return left.boundingBox.midY > right.boundingBox.midY
        }
        return left.boundingBox.minX < right.boundingBox.minX
    }
    let lines = observations.compactMap { observation -> OCRLine? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        let box = observation.boundingBox
        return OCRLine(
            text: text,
            confidence: candidate.confidence,
            x: box.origin.x,
            y: box.origin.y,
            width: box.width,
            height: box.height
        )
    }
    try encode(
        OCRResult(
            engine: "macOS Vision",
            revision: VNRecognizeTextRequest.currentRevision,
            page_index: pageIndex,
            pixel_width: cgImage.width,
            pixel_height: cgImage.height,
            lines: lines
        )
    )
}

do {
    let arguments = Array(CommandLine.arguments.dropFirst())
    guard let command = arguments.first else {
        throw HelperError.usage("Kullanım: KSIOCR capabilities | ocr --input PDF --page-index N --max-dimension N")
    }
    if command == "capabilities" {
        try capabilities()
    } else if command == "ocr" {
        try recognize(arguments: arguments)
    } else {
        throw HelperError.usage("Bilinmeyen OCR komutu.")
    }
} catch {
    FileHandle.standardError.write(Data("Hata: \(error)\n".utf8))
    exit(1)
}
