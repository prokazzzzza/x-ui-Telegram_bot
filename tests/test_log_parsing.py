import re

# The regex currently in bot.py (reconstructed for testing before fix)
# log_pattern = re.compile(r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (\d{1,3}(?:\.\d{1,3}){3}):\d+ accepted email:(\S+)$')

def test_log_regex_matching():
    """
    Test that the regex correctly extracts IP and Email from Xray access log lines.
    """
    # Real log lines from the user's system
    log_lines = [
        "2026/01/19 13:11:31.193164 from 31.29.179.60:43924 accepted tcp:d0.mradx.net:443 [inbound-17343 >> direct] email: tg_824606348",
        "2026/01/19 13:11:31.359229 from tcp:109.184.31.166:49996 accepted udp:255.255.255.255:6537 [inbound-17343 -> blocked] email: tg_1640882574",
        "2026/01/19 13:13:53.305774 from 146.158.11.100:63598 accepted udp:amp-api-edge.apps.apple.com:443 [inbound-17343 >> direct] email: tg_1948009078"
    ]

    # Updated regex to handle:
    # 1. Microseconds in timestamp (.XXXXXX)
    # 2. 'from' keyword
    # 3. 'tcp:' prefix in IP (sometimes present, e.g. tcp:109.184.31.166)
    # 4. Extra content between 'accepted' and 'email:'
    
    # Let's try to define a robust regex
    # Timestamp: ^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?
    # IP: from (?:tcp:|udp:)?(\d{1,3}(?:\.\d{1,3}){3}):\d+
    # Email: email:\s*(\S+)
    
    pattern = re.compile(r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)? from (?:tcp:|udp:)?(\d{1,3}(?:\.\d{1,3}){3}):\d+ accepted .*?email:\s*(\S+)')

    for line in log_lines:
        match = pattern.search(line)
        assert match is not None, f"Failed to match line: {line}"
        
        ip = match.group(1)
        email = match.group(2)
        
        # Verify extraction
        if "31.29.179.60" in line:
            assert ip == "31.29.179.60"
            assert email == "tg_824606348"
        elif "109.184.31.166" in line:
            assert ip == "109.184.31.166"
            assert email == "tg_1640882574"

if __name__ == "__main__":
    # Manual run for debugging
    test_log_regex_matching()
    print("All log tests passed!")
