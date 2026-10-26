// UML model derived from 02Development_Zone/Oracle/JTA_Create_Database.sql and JTA_Packages.sql.
export const classes = [
  { name: "staff", domain: "People", attrs: ["staff_id PK", "location_id FK", "job_id FK", "wage_rate"] },
  { name: "job_posts", domain: "People", attrs: ["job_id PK", "job_title"] },
  { name: "job_posts_history", domain: "People", attrs: ["staff_id FK", "job_id FK"] },
  { name: "work_hours", domain: "People", attrs: ["staff_id FK", "work_date"] },
  { name: "payroll", domain: "People", attrs: ["payroll_id PK", "staff_id FK", "gross_pay", "net_pay"] },
  { name: "locations", domain: "Places", attrs: ["location_id PK", "name", "address"] },
  { name: "office_sections", domain: "Places", attrs: ["office_id PK", "location_id FK", "staff_id FK"] },
  { name: "products", domain: "Catalog", attrs: ["product_id PK", "category_id FK", "unit_measure FK", "tax_code FK", "price_rate"] },
  { name: "product_category", domain: "Catalog", attrs: ["category_id PK", "category_name"] },
  { name: "unit_measures", domain: "Catalog", attrs: ["unit_measure PK"] },
  { name: "tax_rates", domain: "Catalog", attrs: ["tax_code PK", "tax_rate"] },
  { name: "price_history", domain: "Catalog", attrs: ["product_id FK", "start_date"] },
  { name: "inventory_by_location", domain: "Catalog", attrs: ["product_id FK", "location_id FK", "quantity", "reorder_level"] },
  { name: "suppliers", domain: "Procurement", attrs: ["supplier_id PK", "supplier_name"] },
  { name: "suppliers_per_products", domain: "Procurement", attrs: ["supplier_id FK", "product_id FK"] },
  { name: "purchase_orders", domain: "Procurement", attrs: ["po_id PK", "supplier_id FK", "staff_id FK", "location_id FK"] },
  { name: "purchase_order_lines", domain: "Procurement", attrs: ["po_line_id PK", "po_id FK", "product_id FK"] },
  { name: "supplier_invoices", domain: "Procurement", attrs: ["invoice_number PK", "supplier_id FK", "location_id FK", "staff_id FK"] },
  { name: "customer_bills", domain: "Sales", attrs: ["bill_id PK", "assignment_id FK"] },
  { name: "billed_items", domain: "Sales", attrs: ["bill_line_id PK", "bill_id FK", "product_id FK", "tax_code FK"] },
  { name: "cashier_stations", domain: "Sales", attrs: ["station_id PK", "location_id FK"] },
  { name: "cashier_drawer_assignments", domain: "Sales", attrs: ["assignment_id PK", "staff_id FK", "station_id FK"] },
];

// [child, parent, multiplicity on child side, label]
export const relations = [
  ["staff", "locations", "*", "employed at"],
  ["staff", "job_posts", "*", "holds"],
  ["job_posts_history", "staff", "*", "tracks"],
  ["job_posts_history", "job_posts", "*", "tracks"],
  ["work_hours", "staff", "*", "worked by"],
  ["payroll", "staff", "*", "paid to"],
  ["office_sections", "locations", "*", "in"],
  ["office_sections", "staff", "*", "assigned"],
  ["products", "product_category", "*", "belongs to"],
  ["products", "unit_measures", "*", "measured in"],
  ["products", "tax_rates", "*", "taxed by"],
  ["price_history", "products", "*", "prices"],
  ["inventory_by_location", "products", "*", "stocks"],
  ["inventory_by_location", "locations", "*", "held at"],
  ["suppliers_per_products", "suppliers", "*", "supplies"],
  ["suppliers_per_products", "products", "*", "supplied"],
  ["purchase_orders", "suppliers", "*", "ordered from"],
  ["purchase_orders", "staff", "*", "raised by"],
  ["purchase_orders", "locations", "*", "for"],
  ["purchase_order_lines", "purchase_orders", "*", "contains"],
  ["purchase_order_lines", "products", "*", "orders"],
  ["supplier_invoices", "suppliers", "*", "billed by"],
  ["billed_items", "customer_bills", "*", "contains"],
  ["billed_items", "products", "*", "sells"],
  ["billed_items", "tax_rates", "*", "taxed by"],
  ["cashier_stations", "locations", "*", "at"],
  ["cashier_drawer_assignments", "staff", "*", "operated by"],
  ["cashier_drawer_assignments", "cashier_stations", "*", "at"],
  ["customer_bills", "cashier_drawer_assignments", "*", "created in"],
];

export const packages = [
  {
    name: "pkg_jta_error",
    members: ["sp_throw()", "sp_log_error()", "sp_show_in_console()"],
  },
  {
    name: "pkg_jta",
    members: [
      "sp_process_payroll(p_date)", "sp_update_inventory()", "sp_restock_urgent()",
      "fn_get_money_inflow()", "sp_update_taxes()", "fn_get_price_changes()",
      "sp_get_profits_for()", "sp_add_item_to_bill()", "sp_update_from_bill()",
      "sp_stock_check()", "sp_payout()", "fn_get_name()", "... 25 subprograms in total",
    ],
  },
];

// From the sp_process_payroll body: cursor on work_hours, sp_get_hours, wage_rate from staff, insert into payroll.
export const payrollSequence = [
  ["Caller", "pkg_jta", "sp_process_payroll(p_date)"],
  ["pkg_jta", "payroll", "DELETE rows for the same week"],
  ["pkg_jta", "work_hours", "cursor: DISTINCT staff_id for the week"],
  ["pkg_jta", "pkg_jta", "sp_get_hours(staff_id) -> basic/overtime/doubletime"],
  ["pkg_jta", "staff", "SELECT wage_rate"],
  ["pkg_jta", "payroll", "INSERT gross_pay, deductions, net_pay"],
];

export function mermaid(kind) {
  if (kind === "class") {
    const out = ["classDiagram"];
    for (const c of classes) {
      out.push(`  class ${c.name} {`, ...c.attrs.map((a) => `    ${a}`), "  }");
    }
    for (const [child, parent, mult, label] of relations) {
      out.push(`  ${child} "${mult}" --> "1" ${parent} : ${label}`);
    }
    return out.join("\n");
  }
  if (kind === "package") {
    const out = ["classDiagram"];
    for (const p of packages) {
      out.push(`  class ${p.name} {`, "    <<package>>", ...p.members.map((m) => `    ${m}`), "  }");
    }
    out.push("  pkg_jta ..> pkg_jta_error : uses", "  pkg_jta_error ..> jta_errors : writes");
    return out.join("\n");
  }
  if (kind === "sequence") {
    const names = [...new Set(payrollSequence.flatMap(([a, b]) => [a, b]))];
    const out = ["sequenceDiagram", ...names.map((n) => `  participant ${n}`)];
    for (const [from, to, msg] of payrollSequence) out.push(`  ${from}->>${to}: ${msg}`);
    return out.join("\n");
  }
  return null;
}
