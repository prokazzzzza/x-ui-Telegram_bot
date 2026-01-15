
text = "🚀 *Maxi-VPN* — Твой пропуск в свободный интернет!"
print(f"Length: {len(text)}")
encoded = text.encode('utf-8')
print(f"Encoded length: {len(encoded)}")
print(f"Byte at 15: {encoded[15]}")
print(f"Char at 15 (approx): {encoded[15:].decode('utf-8', errors='ignore')[0]}")

print("Offsets:")
for i in range(20):
    print(f"{i}: {encoded[i]:02x} {chr(encoded[i]) if 32 <= encoded[i] <= 126 else '?'}")
