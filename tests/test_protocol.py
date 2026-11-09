import unittest
from whiteboard.protocol import encode, decode_line, ProtocolError

class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        msg={"type":"chat","text":"hello ✓"}
        self.assertEqual(decode_line(encode(msg).rstrip(b"\n")),msg)
    def test_bad_json(self):
        with self.assertRaises(ProtocolError): decode_line(b"{bad")
    def test_missing_type(self):
        with self.assertRaises(ProtocolError): decode_line(b'{"text":"x"}')

if __name__=="__main__": unittest.main()
