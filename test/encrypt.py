from utils.utils import decrypt_phone, encrypt_phone, hash_data  

# phone_number="0193108491"
# encypt_phone_number=encrypt_phone(phone_number)
# print(encypt_phone_number)

# phone_number = "601234567890"
# encrypted_phone = hash_data(phone_number)
# print(f"Encrypted: {encrypted_phone}")

# encrypted_phone='gAAAAABo7RGRvuFpCvCWzG5SV84LrGH3fu-SEBusOJAqFcP4s_HBFZk8Szbcav5ADRIj2Y5ruUveV5AtYzkj__6y2CV5VJAvfw=='
# decrypt_phone_number=decrypt_phone(encrypted_phone)
# print(decrypt_phone_number)

pin = "xxxxx"
encrypted_pin = hash_data(pin)
print(f"Encrypted: {encrypted_pin}")