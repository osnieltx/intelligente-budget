from dotenv import dotenv_values

locals().update(dotenv_values(".env"))
