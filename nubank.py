import pluggy_sdk
from pprint import pprint

from datetime import datetime

import settings

configuration = pluggy_sdk.Configuration()
configuration.api_key['default'] = settings.API_KEY


def transactions(from_date: datetime, to_date: datetime):
    with pluggy_sdk.ApiClient(configuration) as api_client:
        transactions_api = pluggy_sdk.TransactionApi(api_client)
        page_size = 50
        page = 1

        items = []
        try:
            while True:
                api_response = transactions_api.transactions_list(
                    settings.ACCOUNT_ID,
                    var_from=from_date,
                    to=to_date,
                    page_size=page_size,
                    page=page,
                )
                items += api_response.results
                if api_response.total_pages == page:
                    break
                page += 1

        except Exception as e:
            print(
                f"Exception when calling TransactionApi->transactions_list: {e}"
            )
        return items
