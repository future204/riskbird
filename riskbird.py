import httpx, csv, os, sys, datetime, asyncio, argparse, yaml
from tqdm.asyncio import tqdm


class RiskBird:
    def __init__(self, cookie: str = None, max_concurrency: int = 5):
        self.cookie: str = cookie
        if self.cookie == None:
            self.load_config("config.yaml")
        self.ua: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.6833.84 Safari/537.36"
        )
        self.headers: dict = {
            "User-Agent": self.ua,
            "Cookie": str(self.cookie),
            "App-Device": "WEB",
        }
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.batch_mode: bool = False

    def gen_config(self, file: str = "config.yaml"):
        if not os.path.exists(file):
            with open(file, "w", encoding="utf-8") as f:
                f.write("riskbird-cookie: ''\n")
            self.mlog(f"配置文件{file}不存在，已创建")
        else:
            self.mlog(f"配置文件{file}已存在")

    def load_config(self, config_file: str) -> bool:
        try:
            self.gen_config(file=config_file)
            with open(file=config_file, mode="r") as file:
                config: dict = yaml.safe_load(file.read())
                riskbird_cookie = config.get("riskbird-cookie")
                if riskbird_cookie:
                    self.cookie = riskbird_cookie
                    self.mlog(f"加载{config_file}配置文件成功")
                    return True
                else:
                    self.mwarn("cookie 未配置,请配置cookie后重试")
                    sys.exit(1)
        except Exception as e:
            self.merror(f"加载{config_file}配置文件出错 {e}")
            sys.exit(1)
        return False

    @staticmethod
    def timestamp() -> str:
        nowtime: int = int(datetime.datetime.now().timestamp())
        timestamp: str = (
            str(datetime.datetime.fromtimestamp(nowtime))
            .replace(" ", "-")
            .replace(":", "")
        )
        return timestamp

    @staticmethod
    def mlog(message: str, pt: bool = True) -> str:
        message = f"\033[1;32m[INFO]\033[0m {message}"
        if pt:
            print(message)
        return message

    @staticmethod
    def mwarn(message: str, pt: bool = True) -> str:
        message = f"\033[1;33m[WARN]\033[0m {message}"
        if pt:
            print(message)
        return message

    @staticmethod
    def merror(message: str, pt: bool = True) -> str:
        message = f"\033[1;31m[ERROR]\033[0m {message}"
        if pt:
            print(message)
        return message

    def dict_append_to_csvfile(
        self, fieldnames: list, row: dict, filepath: str
    ) -> bool:
        try:
            file_exists = os.path.exists(filepath)
            with open(filepath, "a", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                if not file_exists or os.path.getsize(filepath) == 0:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as e:
            self.merror(f"追加数据到{filepath}出现错误:{e}")
            return False
        return True

    async def search_company(self, client: httpx.AsyncClient, searchKey: str) -> dict:
        if not searchKey:
            return None
        data = {
            "queryType": "1",
            "searchKey": str(searchKey),
            "pageNo": 1,
            "range": 10,
            "selectConditionData": '{"status":"","sort_field":""}',
        }
        url = "https://www.riskbird.com/riskbird-api/newSearch"
        try:
            response = await client.post(
                url, json=data, headers=self.headers, timeout=30
            )
            return response.json()
        except Exception as e:
            self.merror(f"查询 {searchKey} 出错: {e}")
            return None

    def load_company_file(self, filepath: str) -> list[str]:
        try:
            with open(file=filepath, mode="r", encoding="utf-8") as file:
                return [line.strip() for line in file if line.strip()]
        except Exception as e:
            self.merror(f"加载公司列表出现错误：{e}")
            sys.exit(1)

    def deal_info(self, data: dict):
        result = {
            "搜索名称": data.get("搜索名称"),
            "公司名称": None,
            "统一社会信用代码": None,
            "法定代表人": None,
            "注册资本": None,
            "成立日期": None,
            "地址": None,
            "电话": None,
            "邮箱": None,
            "官网": None,
        }
        try:
            if data and data.get("code") == 20000:
                company_list = data["data"]["list"]
                if company_list:
                    tqdm.write(
                        s=self.mlog(
                            f"{data.get('搜索名称')} 搜索到{len(company_list)} 个相关企业 默认选择第一个",
                            pt=False,
                        ),
                        nolock=True,
                    )
                    company: dict = company_list[0]
                    tels = company.get("tels")
                    emails = company.get("emails")
                    website = company.get("website")
                    if isinstance(tels, list):
                        tels = ",".join(tels)
                    if isinstance(emails, list):
                        emails = ",".join(emails)
                    if isinstance(website, list):
                        website = ",".join(website)

                    result.update(
                        {
                            "公司名称": company.get("entName"),
                            "统一社会信用代码": company.get("UNISCID"),
                            "法定代表人": company.get("faren"),
                            "注册资本": company.get("regCap"),
                            "成立日期": company.get("esDate"),
                            "地址": company.get("dom"),
                            "电话": tels,
                            "邮箱": emails,
                            "官网": website,
                        }
                    )
        except Exception as e:
            self.merror(f"处理查询数据出错\n{data}")
            sys.exit(1)
        return result

    async def fetch_and_save(self, client, search_company: str, filepath: str):
        async with self.semaphore:
            data = await self.search_company(client, search_company)
            data["搜索名称"] = search_company
            company_info: dict = self.deal_info(data)
            self.dict_append_to_csvfile(
                fieldnames=company_info.keys(), row=company_info, filepath=filepath
            )
            return company_info

    async def batch_company_info(self, company_file: str):
        self.batch_mode = True
        company_list = self.load_company_file(company_file)
        filepath = f"results-{self.timestamp()}.csv"
        async with httpx.AsyncClient(verify=False) as client:
            results = []
            for coro in tqdm.as_completed(
                [
                    self.fetch_and_save(client, company, filepath)
                    for company in company_list
                ],
                total=len(company_list),
                desc="查询进度",
            ):
                result = await coro
                results.append(result)
            self.mlog(f"成功保存结果到{(os.path.abspath(filepath))}")

        return results

    async def get_company_info(self, search_company: str):
        self.batch_mode = False
        async with httpx.AsyncClient(verify=False) as client:
            data = await self.search_company(client, search_company)
            data["搜索名称"] = search_company
            company_info: dict = self.deal_info(data)
            for k in company_info:
                self.mlog(f"{k}:{company_info[k]}")
            return company_info


def main():
    parser = argparse.ArgumentParser(description="RiskBird 公司信息查询工具")
    parser.add_argument("-n", "--name", type=str, help="查询单个公司信息")
    parser.add_argument("-f", "--file", type=str, help="批量查询公司列表文件")
    parser.add_argument("-c", "--cookie", type=str, required=False, help="认证 Cookie")
    parser.add_argument("-m", "--max", type=int, default=5, help="最大并发数 (默认 5)")

    args = parser.parse_args()
    cookie = None
    if args.cookie:
        cookie = args.cookie
    rb = RiskBird(cookie=cookie, max_concurrency=args.max)
    if args.name:
        asyncio.run(rb.get_company_info(args.name))
    elif args.file:
        asyncio.run(rb.batch_company_info(args.file))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
