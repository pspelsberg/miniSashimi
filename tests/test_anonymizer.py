from app.middleware.anonymizer import Anonymizer

def test_anonymizer_mask_and_unmask():
    anonymizer = Anonymizer()
    
    # Text with various PII categories
    text = (
        "Hallo Herr Schmidt, bitte senden Sie die API-Keys an john.doe@example.com. "
        "Die Server-IP ist 192.168.100.22 und der API-Key lautet sk-ant-abcdefghijklmnopqrstuvwxyz12345678901234. "
        "Telefonnummer für Rückfragen: +49 123 456789."
    )
    
    masked = anonymizer.mask(text)
    
    # Assert placeholders are present
    assert "[NAME_" in masked
    assert "[EMAIL_" in masked
    assert "[IP_" in masked
    assert "[API_KEY_" in masked
    assert "[PHONE_" in masked
    
    # Assert original sensitive values are NOT in the masked text
    assert "Schmidt" not in masked
    assert "john.doe" not in masked
    assert "192.168.100.22" not in masked
    assert "sk-ant" not in masked
    assert "+49" not in masked
    
    # Test de-anonymization
    unmasked = anonymizer.unmask(masked)
    assert unmasked == text
    
    # Test with more than 9 placeholders of the same type to verify prefix replacement safety
    many_emails_text = " ".join([f"test{i}@example.com" for i in range(1, 15)])
    masked_many = anonymizer.mask(many_emails_text)
    assert "[EMAIL_10]" in masked_many
    assert "[EMAIL_14]" in masked_many
    unmasked_many = anonymizer.unmask(masked_many)
    assert unmasked_many == many_emails_text
