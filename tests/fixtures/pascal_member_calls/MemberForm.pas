unit MemberForm;

interface

type
  TMemberForm = class(TObject)
  public
    procedure Btn;
  end;

implementation

procedure TMemberForm.Btn;
var
  Query: TObject;
begin
  Query.Refresh();
end;

end.
