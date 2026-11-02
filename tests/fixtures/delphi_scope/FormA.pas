unit FormA;

interface

uses Basis;

type
  TfrmA = class(TfrmBasis)
  private
    F: array of Integer;
  public
    procedure Doe;
    procedure Verander;
  end;

implementation

procedure TfrmA.Doe;
var
  X, Y, i: Integer;
begin
  Verander;
  Geheim;
  Laad(1);
  Laad(X, Y);
  Laad(F[i]);
end;

procedure TfrmA.Verander;
begin
  inherited Verander;
end;

end.
