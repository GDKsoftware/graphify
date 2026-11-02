unit Decls;

interface

uses
  Classes, Forms, StdCtrls;

type
  TCallback = procedure(X: Integer) of object;

  TfrmBasis = class(TForm)
    Button1: TButton;
  private
    procedure Geheim;
  strict private
    procedure HeelGeheim;
  protected
    FOnChange: TNotifyEvent;
  public
    procedure Verander; overload;
    procedure Verander(A: Integer; B: Integer = 0); overload;
    property Helper: THelper read FHelper;
  end;

  TPunt = record
    X: Integer;
  end;

var
  frmBasis: TfrmBasis;

function Maandnaam(Nr: Integer): string;

implementation

uses
  Helpers;

{$I shared.inc}

function Intern: Integer;
begin
  Result := 0;
end;

function Maandnaam(Nr: Integer): string;
begin
  Result := '';
end;

procedure TfrmBasis.Geheim;
begin
end;

procedure TfrmBasis.HeelGeheim;
begin
end;

procedure TfrmBasis.Verander;
  procedure Stap;
  begin
  end;
begin
  Stap;
end;

procedure TfrmBasis.Verander(A: Integer; B: Integer);
begin
end;

end.
