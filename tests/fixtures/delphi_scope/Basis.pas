unit Basis;

interface

uses Forms;

type
  TfrmBasis = class(TForm)
  published
    procedure Verander;
  private
    procedure Geheim;
  strict private
    procedure HeelGeheim;
  public
    procedure Laad; overload;
    procedure Laad(A: Integer); overload;
    procedure Laad(A, B: Integer); overload;
  end;

  TfrmLokaal = class(TfrmBasis)
  public
    procedure Doe;
  end;

implementation

procedure TfrmBasis.Verander;
begin
end;

procedure TfrmBasis.Geheim;
begin
end;

procedure TfrmBasis.HeelGeheim;
begin
end;

procedure TfrmBasis.Laad;
begin
end;

procedure TfrmBasis.Laad(A: Integer);
begin
end;

procedure TfrmBasis.Laad(A, B: Integer);
begin
end;

procedure TfrmLokaal.Doe;
begin
  Geheim;
  HeelGeheim;
end;

end.
