#!/usr/bin/env swift
// OCR 图片 → JSON: { "<图片路径>": [ {text, conf, box:[x0,y0,x1,y1]}, ... ] }
// box 为归一化坐标(0~1)、原点左上（已从 Vision 的左下原点转换），供 Python 侧做行/列还原。
// 依赖：macOS 10.15+（Vision 框架），支持中文(zh-Hans)与英文。
// 用法：swift ocr_vision.swift img1.png img2.png ...
import Foundation
import Vision
import AppKit

func ocr(_ url: URL) -> [[String: Any]] {
    guard let img = NSImage(contentsOf: url),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        return []
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return []
    }
    var words: [[String: Any]] = []
    if let results = request.results {
        for obs in results {
            if let cand = obs.topCandidates(1).first {
                // Vision boundingBox: 归一化、原点左下 → 转为左上原点
                let b = obs.boundingBox
                let x0 = b.origin.x
                let x1 = b.origin.x + b.width
                let y0 = 1.0 - b.origin.y - b.height
                let y1 = 1.0 - b.origin.y
                words.append([
                    "text": cand.string,
                    "conf": Double(cand.confidence),
                    "box": [Double(x0), Double(y0), Double(x1), Double(y1)],
                ])
            }
        }
    }
    return words
}

let args = CommandLine.arguments.dropFirst()
var out: [String: [[String: Any]]] = [:]
for arg in args {
    out[arg] = ocr(URL(fileURLWithPath: arg))
}

let data = try! JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
