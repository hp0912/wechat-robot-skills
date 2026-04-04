# wechat-robot-skills

微信机器人 Skills

**系统自动注入的环境变量**

ROBOT_WECHAT_CLIENT_PORT: 机器人客户端服务端口，可用于在 SKILL 脚本直接调用客户端接口 `http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/xxxxx`
ROBOT_ID: 机器人实例 ID
ROBOT_CODE: 机器人实例编码
ROBOT_REDIS_DB: 机器人的 Redis DB
ROBOT_WX_ID: 机器人的微信 ID
ROBOT_FROM_WX_ID: 微信消息来源(群聊 ID 或者好友微信 ID)
ROBOT_SENDER_WX_ID: 微信消息发送人的微信 ID
ROBOT_MESSAGE_ID: 微信消息 ID
ROBOT_REF_MESSAGE_ID: 如果是引用消息，则是引用的消息的 ID

**需要用户手动注入的环境变量，执行脚本只负责读，环境变量由用户在 UI 界面写入，当脚本需要操作 mysql 数据库的时候会用到**

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=houhou

**需要发送图片的时候可以在控制台输出如下内容**

```
<wechat-robot-image-url>图片URL1</wechat-robot-image-url>
<wechat-robot-image-url>图片URL2</wechat-robot-image-url>
<wechat-robot-image-url>图片URL3</wechat-robot-image-url>
<wechat-robot-image-url>图片URL4</wechat-robot-image-url>
```
