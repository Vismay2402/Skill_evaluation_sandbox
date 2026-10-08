create or replace view analytics.v_latest_orders as
select * from (
  select o.*, c.segment,
         row_number() over (partition by o.order_id order by o.updated_at desc) as rn
  from orders o
  join customers c on o.customer_id = c.customer_id
  where o.created_at > dateadd(day, -30, current_timestamp())
)
where rn = 1;
