import Foundation

/// Lenient decoders for an API whose SQLite rows surface as strings.
///
/// The live responses mix conventions: `created_at` is an epoch number in
/// `/sessions` but an ISO string in `/sessions/{id}`; `archived` arrives
/// as 0/1; `pinned` as "True"/"False"; `seq` as "67". These helpers accept
/// every observed variant instead of hard-failing the whole array decode.
enum Lenient {
    static func intValue<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) -> Int? {
        if let i = try? c.decode(Int.self, forKey: key) { return i }
        if let d = try? c.decode(Double.self, forKey: key) { return Int(d) }
        if let s = try? c.decode(String.self, forKey: key) {
            if let i = Int(s) { return i }
            if let d = Double(s) { return Int(d) }
        }
        return nil
    }

    static func boolValue<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) -> Bool? {
        if let b = try? c.decode(Bool.self, forKey: key) { return b }
        if let i = try? c.decode(Int.self, forKey: key) { return i != 0 }
        if let s = try? c.decode(String.self, forKey: key) {
            switch s.trimmingCharacters(in: .whitespaces).lowercased() {
            case "true", "1", "yes": return true
            case "false", "0", "no": return false
            default: return nil
            }
        }
        return nil
    }

    /// Epoch seconds from a Double epoch, numeric string, or ISO-8601 string.
    static func epochValue<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) -> Double? {
        if let d = try? c.decode(Double.self, forKey: key) { return d }
        if let s = try? c.decode(String.self, forKey: key) {
            if let d = Double(s) { return d }
            let iso = ISO8601DateFormatter()
            if let date = iso.date(from: s) ?? iso.date(from: s + "Z") {
                return date.timeIntervalSince1970
            }
        }
        return nil
    }

    static func isoString<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) -> String? {
        try? c.decode(String.self, forKey: key)
    }
}
