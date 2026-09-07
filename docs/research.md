# 功能与格式依据

本项目按公开功能及互操作格式实现，没有反编译或复制楼月安装包、注册逻辑、界面资源或品牌。

- 产品流程参考：https://www.louyue.com/pcwxdaochu.htm
- SQLCipher 设计与参数：https://www.zetetic.net/sqlcipher/design/
- SQLite 数据库及 WAL 格式：https://sqlite.org/fileformat2.html
- Frida 官方 API：https://frida.re/docs/javascript-api/
- 微信 4.x 公开研究：https://github.com/tzwkb/wechat-decrypt
- 启动时 HMAC 输入观察方法：https://github.com/tzwkb/wechat-decrypt/blob/main/scripts/windows/extract_raw_key.py
- 多分库消息和联系人字段：https://github.com/tzwkb/wechat-decrypt/blob/main/db.py
- 图片 V2 格式及资源表映射：https://github.com/emcd39/wechat-cli/tree/main/src/attachment
- 合并转发的记录项结构：https://github.com/wechaty/wechaty/issues/1679
- 引用图片与原消息关系：本机存档只读验证，按同一会话内 refermsg/svrid 与 server_id 精确匹配；不以时间近似匹配。

源码均为本项目组织的 Python/Qt 实现。互操作思路受到以上公开研究启发；对应开源声明随项目保留。
