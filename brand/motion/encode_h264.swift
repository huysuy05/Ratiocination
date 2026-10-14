// Reads raw 8-bit grayscale frames (width × height bytes each) from stdin and writes an H.264 MP4
// with AVFoundation. Usage: encode_h264 <out.mp4> <width> <height> <fps> <bitrate>
import AVFoundation
import CoreVideo
import Foundation

let args = CommandLine.arguments
guard args.count == 6, let width = Int(args[2]), let height = Int(args[3]), let fps = Int32(args[4]), let bitrate = Int(args[5]) else {
    FileHandle.standardError.write("usage: encode_h264 <out.mp4> <width> <height> <fps> <bitrate>\n".data(using: .utf8)!)
    exit(2)
}
let output = URL(fileURLWithPath: args[1])
try? FileManager.default.removeItem(at: output)

let writer = try AVAssetWriter(outputURL: output, fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: width,
    AVVideoHeightKey: height,
    AVVideoColorPropertiesKey: [
        AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
        AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
        AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
    ],
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: bitrate,
        AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
        AVVideoMaxKeyFrameIntervalKey: Int(fps) * 2,
        AVVideoAllowFrameReorderingKey: true,
        AVVideoExpectedSourceFrameRateKey: Int(fps),
    ],
])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
    kCVPixelBufferWidthKey as String: width,
    kCVPixelBufferHeightKey as String: height,
])
writer.add(input)
guard writer.startWriting() else { fatalError("startWriting: \(String(describing: writer.error))") }
writer.startSession(atSourceTime: .zero)

// Gray (full range 0–255) → video-range luma (16–235).
var luma = [UInt8](repeating: 0, count: 256)
for g in 0..<256 { luma[g] = UInt8(16 + (g * 219 + 127) / 255) }

let frameBytes = width * height
let stdin = FileHandle.standardInput
var frames: Int64 = 0
while true {
    var data = Data(capacity: frameBytes)
    while data.count < frameBytes {
        let chunk = stdin.readData(ofLength: frameBytes - data.count)
        if chunk.isEmpty { break }
        data.append(chunk)
    }
    if data.count < frameBytes { break }
    while !input.isReadyForMoreMediaData { usleep(2000) }
    var buffer: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &buffer)
    guard let pixels = buffer else { fatalError("no pixel buffer") }
    CVPixelBufferLockBaseAddress(pixels, [])
    let yBase = CVPixelBufferGetBaseAddressOfPlane(pixels, 0)!.assumingMemoryBound(to: UInt8.self)
    let yStride = CVPixelBufferGetBytesPerRowOfPlane(pixels, 0)
    data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
        let src = raw.bindMemory(to: UInt8.self)
        for row in 0..<height {
            let line = yBase + row * yStride
            let from = row * width
            for col in 0..<width { line[col] = luma[Int(src[from + col])] }
        }
    }
    let cBase = CVPixelBufferGetBaseAddressOfPlane(pixels, 1)!
    let cStride = CVPixelBufferGetBytesPerRowOfPlane(pixels, 1)
    for row in 0..<(height / 2) { memset(cBase + row * cStride, 128, width) }
    CVPixelBufferUnlockBaseAddress(pixels, [])
    if !adaptor.append(pixels, withPresentationTime: CMTime(value: frames, timescale: fps)) {
        fatalError("append failed: \(String(describing: writer.error))")
    }
    frames += 1
}
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
guard writer.status == .completed else { fatalError("finish: \(String(describing: writer.error))") }
print("wrote \(frames) frames to \(output.path)")
