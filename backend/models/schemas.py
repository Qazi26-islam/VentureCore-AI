from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class ResearchRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="A business question, at least 10 characters.",
    )
    mode: str = Field(default="market", description="'market' or 'validate'")
    depth: str = Field(default="standard", description="'quick', 'standard', or 'deep'")
    use_company_profile: bool = Field(default=False)


class StartResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    status: str
    stage: str
    sections: Dict[str, str]
    report: Optional[str] = None
    error: Optional[str] = None


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str


class HistoryItem(BaseModel):
    id: str
    question: str
    title: Optional[str] = None
    favorite: bool = False
    created_at: str


class MessageItem(BaseModel):
    role: str
    content: str


class JobDetailResponse(BaseModel):
    id: str
    question: str
    title: Optional[str] = None
    favorite: bool = False
    report: Optional[str] = None
    sections: Dict[str, str]
    messages: List[MessageItem]


class FollowUpRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


class FollowUpResponse(BaseModel):
    reply: str


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class FavoriteRequest(BaseModel):
    favorite: bool


class OpportunityRequest(BaseModel):
    query: str = Field(..., min_length=10, max_length=300)


class OpportunityItem(BaseModel):
    opportunity: str
    market: str
    difficulty: str
    potential: int


class OpportunityResponse(BaseModel):
    items: List[OpportunityItem]


class CompanyProfileRequest(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=160)
    industry: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=120)
    currency: str = Field(default="MYR", max_length=12)
    products_services: str = Field(default="", max_length=500)
    target_customers: str = Field(default="", max_length=500)
    main_competitors: str = Field(default="", max_length=500)
    monthly_budget: str = Field(default="", max_length=100)
    business_goals: str = Field(default="", max_length=700)
    business_stage: str = Field(default="Existing business", max_length=80)


class CompanyProfileResponse(CompanyProfileRequest):
    id: int
    updated_at: str


class DataQualityResponse(BaseModel):
    filename: str
    data_type: str
    row_count: int
    column_count: int
    columns: List[str]
    missing_values: int
    duplicate_rows: int
    invalid_numeric_values: int
    warnings: List[str]


class SupplierRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    contact_name: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=60)
    lead_time_days: int = Field(default=7, ge=0, le=365)
    payment_terms: str = Field(default="", max_length=120)


class SupplierItem(SupplierRequest):
    id: int


class ProductRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=180)
    sku: str = Field(default="", max_length=80)
    category: str = Field(default="", max_length=100)
    supplier_id: Optional[int] = None
    unit_cost: float = Field(default=0, ge=0)
    selling_price: float = Field(default=0, ge=0)
    reorder_point: float = Field(default=0, ge=0)
    lead_time_days: int = Field(default=7, ge=0, le=365)


class StockMovementRequest(BaseModel):
    transaction_type: str = Field(..., pattern="^(received|sold|damaged|adjustment)$")
    quantity: float = Field(..., gt=0)
    reference_note: str = Field(default="", max_length=300)
    unit_cost: Optional[float] = Field(default=None, ge=0)


class InventoryItem(BaseModel):
    id: int
    sku: str
    name: str
    category: str
    current_stock: float
    unit_cost: float
    selling_price: float
    inventory_value: float
    reorder_point: float
    lead_time_days: int
    supplier_name: Optional[str] = None
    units_sold_30d: float
    average_daily_sales: float
    days_of_stock: Optional[float] = None
    recommended_reorder_quantity: int
    estimated_reorder_cost: float
    status: str


class InventoryQuestionRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=600)


class InventoryQuestionResponse(BaseModel):
    answer: str


class CustomerRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    email: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=60)
    segment: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=500)


class CustomerItem(CustomerRequest):
    id: int


class SaleRequest(BaseModel):
    product_id: int
    customer_id: Optional[int] = None
    quantity: float = Field(..., gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    payment_status: str = Field(default="paid", pattern="^(paid|due)$")
    due_date: Optional[str] = Field(default=None, max_length=10)
    reference_note: str = Field(default="", max_length=300)


class SaleRecord(BaseModel):
    id: int
    customer_name: Optional[str] = None
    product_name: str
    quantity: float
    unit_price: float
    total_amount: float
    payment_status: str
    due_date: Optional[str] = None
    created_at: str


class SalesDashboardResponse(BaseModel):
    revenue_30d: float
    cash_collected_30d: float
    outstanding_amount: float
    orders_30d: int
    top_customer: Optional[str] = None
    recent_sales: List[SaleRecord]
