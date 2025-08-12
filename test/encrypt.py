from utils.utils import encrypt_phone, hash_data  

# phone_number="0193108491"
# encypt_phone_number=encrypt_phone(phone_number)
# print(encypt_phone_number)

phone_number = "60183553589"
encrypted_phone = encrypt_phone(phone_number)
print(f"Encrypted: {encrypted_phone}")