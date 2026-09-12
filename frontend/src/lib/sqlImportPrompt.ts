/**
 * The brief to hand an assistant that is writing SQL for the importer.
 *
 * The rules in here are not style preferences. Each one exists because the
 * importer reads uploaded SQL with a parser, and output that breaks the rule
 * imports worse -- usually in a way that is recoverable but annoying, twice in
 * a way that is not:
 *
 *   `CREATE TABLE <name> AS`   without it the model is named after the file,
 *                              so `03_report_v2.sql` becomes a model called
 *                              `report_v2`
 *   qualified table names      `orders` alone is assumed to live in the
 *                              project's source schema, which may not be where
 *                              it is
 *
 * Written in both languages because it is read before it is used, and somebody
 * about to paste three hundred words into a chat window is entitled to know
 * what they say.
 */

export const SQL_IMPORT_PROMPT: Record<'en' | 'vi', string> = {
  en: `You are writing SQL that will be imported into a dbt project by AppBI.

Follow the rules below exactly. They are not style preferences: the importer
reads your output with a parser, and output that breaks them imports badly.

## Output format

- One query per file. Name each file <model_name>.sql.
- Begin every file with:  CREATE TABLE <model_name> AS
  <model_name> is lower case letters, digits and underscores, starting with a
  letter. It becomes the dbt model name, so make it say what one row is:
  stg_orders, daily_revenue, customer_lifetime_value.
- Write exactly one SELECT, ending in one semicolon. Nothing after it.
- Never use INSERT, UPDATE, DELETE, MERGE, TRUNCATE, CREATE INDEX, GRANT or
  SET. A dbt model owns what it writes, so those cannot become one and will
  be skipped with a message.

## Naming the tables a query reads

- Always write a table as schema.table. Never bare.
- Data already in the warehouse: use its real schema and table name. The
  importer declares these as dbt sources.
- A result produced by ANOTHER file in the same batch: write exactly the
  <model_name> that file creates. That is how the build order is worked out,
  and it is the only way two of your files end up connected.
- Do not invent a table name. If you do not know one, ask me for it.

## Writing the query

- List the columns. Avoid SELECT * -- the importer cannot see the columns of
  a table it has not read, so it cannot suggest tests for them.
- Alias with AS:  FROM schema.orders AS o,  SELECT o.total AS amount.
- Avoid table functions in FROM (generate_series, a UDF). They read as
  tables to a parser.
- Comments are welcome. They are kept exactly as you write them.
- Do not add a WHERE that filters by a date you invented. If incremental
  behaviour is wanted, say so in a comment and leave the query full-refresh.

## Example of correct output

File stg_orders.sql:

    -- One row per paid order.
    CREATE TABLE stg_orders AS
    SELECT
        o.id          AS order_id,
        o.customer_id,
        o.total,
        o.created_at  AS ordered_at
    FROM raw.orders AS o
    WHERE o.status = 'paid';

File daily_revenue.sql:

    -- One row per country per day.
    CREATE TABLE daily_revenue AS
    SELECT
        c.country,
        date_trunc('day', o.ordered_at) AS day,
        sum(o.total)                    AS revenue
    FROM stg_orders AS o
    JOIN raw.customers AS c ON c.id = o.customer_id
    GROUP BY 1, 2;

Note that daily_revenue reads stg_orders by the name stg_orders creates, and
reads raw.customers by its real name. That is the whole convention.

## What I want

Warehouse: <BigQuery or PostgreSQL>
Tables I have: <schema.table, and the columns that matter>
What I want out: <describe the result>`,

  vi: `Bạn đang viết SQL để nhập vào một dự án dbt bằng AppBI.

Hãy tuân thủ đúng các quy tắc dưới đây. Đây không phải sở thích trình bày:
trình nhập đọc kết quả của bạn bằng một parser, viết sai quy tắc thì nhập vào
sẽ lệch.

## Định dạng đầu ra

- Mỗi file một query. Đặt tên file là <ten_model>.sql.
- Bắt đầu mỗi file bằng:  CREATE TABLE <ten_model> AS
  <ten_model> gồm chữ thường, số và gạch dưới, bắt đầu bằng chữ. Nó sẽ thành
  tên model dbt, nên hãy đặt sao cho nói rõ MỘT DÒNG là gì: stg_orders,
  daily_revenue, customer_lifetime_value.
- Viết đúng một câu SELECT, kết thúc bằng một dấu chấm phẩy. Không có gì sau đó.
- Tuyệt đối không dùng INSERT, UPDATE, DELETE, MERGE, TRUNCATE, CREATE INDEX,
  GRANT hay SET. Một model dbt sở hữu thứ nó ghi ra, nên những câu đó không
  thể thành model và sẽ bị bỏ qua kèm lý do.

## Cách gọi tên bảng mà query đọc

- Luôn viết đủ schema.table. Không bao giờ để trống schema.
- Dữ liệu sẵn có trong kho: dùng đúng tên schema và tên bảng thật. Trình nhập
  sẽ khai báo chúng thành source của dbt.
- Kết quả do MỘT FILE KHÁC trong cùng lô tạo ra: viết đúng <ten_model> mà file
  đó tạo. Đó là cách thứ tự build được suy ra, và là cách duy nhất để hai file
  của bạn nối được với nhau.
- Không bịa tên bảng. Không biết tên thì hỏi lại tôi.

## Cách viết query

- Liệt kê cột ra. Tránh SELECT * — trình nhập không nhìn thấy cột của bảng mà
  nó chưa đọc, nên cũng không gợi ý được test cho chúng.
- Đặt alias bằng AS:  FROM schema.orders AS o,  SELECT o.total AS amount.
- Tránh dùng hàm trả về bảng trong FROM (generate_series, UDF). Parser sẽ đọc
  chúng như tên bảng.
- Comment thì cứ viết. Chúng được giữ nguyên từng ký tự.
- Đừng thêm WHERE lọc theo một mốc ngày bạn tự nghĩ ra. Nếu muốn chạy tăng
  dần, hãy ghi vào comment và để query chạy toàn bộ.

## Ví dụ đầu ra đúng

File stg_orders.sql:

    -- Mỗi dòng là một đơn đã thanh toán.
    CREATE TABLE stg_orders AS
    SELECT
        o.id          AS order_id,
        o.customer_id,
        o.total,
        o.created_at  AS ordered_at
    FROM raw.orders AS o
    WHERE o.status = 'paid';

File daily_revenue.sql:

    -- Mỗi dòng là một quốc gia trong một ngày.
    CREATE TABLE daily_revenue AS
    SELECT
        c.country,
        date_trunc('day', o.ordered_at) AS day,
        sum(o.total)                    AS revenue
    FROM stg_orders AS o
    JOIN raw.customers AS c ON c.id = o.customer_id
    GROUP BY 1, 2;

Để ý: daily_revenue đọc stg_orders bằng đúng cái tên mà stg_orders tạo ra, và
đọc raw.customers bằng tên thật của nó. Toàn bộ quy ước chỉ có vậy.

## Điều tôi cần

Kho dữ liệu: <BigQuery hay PostgreSQL>
Bảng tôi đang có: <schema.table, và các cột quan trọng>
Kết quả tôi muốn: <mô tả kết quả>`,
};
